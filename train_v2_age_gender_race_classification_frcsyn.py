import argparse
import logging
import os, sys
from datetime import datetime

import random
import numpy as np
import torch
from backbones import get_model
from dataset import get_dataloader
from losses import CombinedMarginLoss
from lr_scheduler import PolyScheduler
from partial_fc_v2 import PartialFC_V2, PartialFC_V2_INVERSE
from torch import distributed
from torch.utils.data import DataLoader
from torch.utils.tensorboard import SummaryWriter
# from utils.utils_callbacks import CallBackLogging, CallBackVerification
from utils.utils_callbacks_frcsyn import CallBackLogging, CallBackVerification
from utils.utils_config import get_config
from utils.utils_distributed_sampler import setup_seed
from utils.utils_logging import AverageMeter, init_logging

# assert torch.__version__ >= "1.12.0", "In order to enjoy the features of the new torch, \
# we have upgraded the torch to 1.12.0. torch before than 1.12.0 may not work in the future."

try:
    rank = int(os.environ["RANK"])
    local_rank = int(os.environ["LOCAL_RANK"])
    world_size = int(os.environ["WORLD_SIZE"])
    distributed.init_process_group("nccl")
except KeyError:
    rank = 0
    local_rank = 0
    world_size = 1
    distributed.init_process_group(
        backend="nccl",
        # init_method="tcp://127.0.0.1:12584",
        init_method="tcp://127.0.0.1:" + str(int(random.random() * 10000 + 12000)),    # Bernardo
        rank=rank,
        world_size=world_size,
    )


def main(args):

    run_name = datetime.now().strftime("%Y-%m-%d_%H-%M-%S") + f"_GPU{rank}"  # Bernardo
    run_name += f'_{args.annotation}' if args.annotation != '' else ''  # Bernardo

    # get config
    cfg = get_config(args.config, run_name)
    # global control random seed
    setup_seed(seed=cfg.seed, cuda_deterministic=False)

    torch.cuda.set_device(local_rank)

    os.makedirs(cfg.output, exist_ok=True)
    init_logging(rank, cfg.output)

    summary_writer = (
        SummaryWriter(log_dir=os.path.join(cfg.output, "tensorboard"))
        if rank == 0
        else None
    )
    
    wandb_logger = None
    if cfg.using_wandb:
        import wandb
        # Sign in to wandb
        try:
            wandb.login(key=cfg.wandb_key)
        except Exception as e:
            print("WandB Key must be provided in config file (base.py).")
            print(f"Config Error: {e}")
        # Initialize wandb
        # run_name = datetime.now().strftime("%y%m%d_%H%M") + f"_GPU{rank}"
        run_name = run_name if cfg.suffix_run_name is None else run_name + f"_{cfg.suffix_run_name}"
        try:
            wandb_logger = wandb.init(
                entity = cfg.wandb_entity, 
                project = cfg.wandb_project, 
                sync_tensorboard = True,
                resume=cfg.wandb_resume,
                name = run_name, 
                notes = cfg.notes) if rank == 0 or cfg.wandb_log_all else None
            if wandb_logger:
                wandb_logger.config.update(cfg)
        except Exception as e:
            print("WandB Data (Entity and Project name) must be provided in config file (base.py).")
            print(f"Config Error: {e}")
            
    train_loader = get_dataloader(
        cfg.rec,
        local_rank,
        cfg.batch_size,
        cfg.dali,
        cfg.seed,
        cfg.num_workers
    )

    backbone = get_model(
        cfg.network, dropout=0.0, fp16=cfg.fp16, num_features=cfg.embedding_size).cuda()
    backbone = torch.nn.parallel.DistributedDataParallel(
        module=backbone, broadcast_buffers=False, device_ids=[local_rank], bucket_cap_mb=16,
        find_unused_parameters=True)
    backbone.train()
    # FIXME using gradient checkpoint if there are some unused parameters will cause error
    backbone._set_static_graph()

    # # Discriminator of ethnic groups
    # backbone_race = get_model(
    #     'r18_1x512', dropout=0.0, fp16=cfg.fp16, num_features=256).cuda()
    # backbone_race = torch.nn.parallel.DistributedDataParallel(
    #     module=backbone_race, broadcast_buffers=False, device_ids=[local_rank], bucket_cap_mb=16,
    #     find_unused_parameters=True)
    # backbone_race.train()
    # backbone_race._set_static_graph()

    # # Discriminator of gender
    # backbone_gender = get_model(
    #     'r18_1x512', dropout=0.0, fp16=cfg.fp16, num_features=256).cuda()
    # backbone_gender = torch.nn.parallel.DistributedDataParallel(
    #     module=backbone_gender, broadcast_buffers=False, device_ids=[local_rank], bucket_cap_mb=16,
    #     find_unused_parameters=True)
    # backbone_gender.train()
    # backbone_gender._set_static_graph()

    # # Discriminator of age
    # backbone_age = get_model(
    #     'r18_1x512', dropout=0.0, fp16=cfg.fp16, num_features=256).cuda()
    # backbone_age = torch.nn.parallel.DistributedDataParallel(
    #     module=backbone_age, broadcast_buffers=False, device_ids=[local_rank], bucket_cap_mb=16,
    #     find_unused_parameters=True)
    # backbone_age.train()
    # backbone_age._set_static_graph()


    margin_loss = CombinedMarginLoss(
        64,
        cfg.margin_list[0],
        cfg.margin_list[1],
        cfg.margin_list[2],
        cfg.interclass_filtering_threshold
    )

    if cfg.optimizer == "sgd":
        module_partial_fc = PartialFC_V2(
            margin_loss, cfg.embedding_size, cfg.num_classes,
            cfg.sample_rate, cfg.fp16)
        module_partial_fc.train().cuda()

        module_partial_fc_race = PartialFC_V2(
            margin_loss, 512, 6,
            cfg.sample_rate, cfg.fp16)
        module_partial_fc_race.train().cuda()

        module_partial_fc_gender = PartialFC_V2(
            margin_loss, 512, 2,
            cfg.sample_rate, cfg.fp16)
        module_partial_fc_gender.train().cuda()

        module_partial_fc_age = PartialFC_V2(
            margin_loss, 512, 6,
            cfg.sample_rate, cfg.fp16)
        module_partial_fc_age.train().cuda()

        # TODO the params of partial fc must be last in the params list
        opt = torch.optim.SGD(
            params=[{"params": backbone.parameters()}, {"params": module_partial_fc.parameters()}],
            lr=cfg.lr, momentum=0.9, weight_decay=cfg.weight_decay)

    elif cfg.optimizer == "adamw":
        module_partial_fc = PartialFC_V2(
            margin_loss, cfg.embedding_size, cfg.num_classes,
            cfg.sample_rate, cfg.fp16)
        module_partial_fc.train().cuda()
        opt = torch.optim.AdamW(
            params=[{"params": backbone.parameters()}, {"params": module_partial_fc.parameters()}],
            lr=cfg.lr, weight_decay=cfg.weight_decay)
    else:
        raise

    cfg.total_batch_size = cfg.batch_size * world_size
    cfg.warmup_step = cfg.num_image // cfg.total_batch_size * cfg.warmup_epoch
    cfg.total_step = cfg.num_image // cfg.total_batch_size * cfg.num_epoch

    lr_scheduler = PolyScheduler(
        optimizer=opt,
        base_lr=cfg.lr,
        max_steps=cfg.total_step,
        warmup_steps=cfg.warmup_step,
        last_epoch=-1
    )

    start_epoch = 0
    global_step = 0
    # if cfg.resume:
    if args.resume != '' and os.path.isfile(args.resume):
        print(f'Loading pre-trained model \'{args.resume}\'')
        # dict_checkpoint = torch.load(os.path.join(cfg.output, f"checkpoint_gpu_{rank}.pt"))
        dict_checkpoint = torch.load(args.resume)
        start_epoch = dict_checkpoint["epoch"]
        global_step = dict_checkpoint["global_step"]
        backbone.module.load_state_dict(dict_checkpoint["state_dict_backbone"])
        # backbone_race.module.load_state_dict(dict_checkpoint["state_dict_backbone_race"])
        # backbone_gender.module.load_state_dict(dict_checkpoint["state_dict_backbone_gender"])
        # backbone_age.module.load_state_dict(dict_checkpoint["state_dict_backbone_age"])
        module_partial_fc.load_state_dict(dict_checkpoint["state_dict_softmax_fc"])
        module_partial_fc_race.load_state_dict(dict_checkpoint["state_dict_softmax_fc_race"])
        module_partial_fc_gender.load_state_dict(dict_checkpoint["state_dict_softmax_fc_gender"])
        module_partial_fc_age.load_state_dict(dict_checkpoint["state_dict_softmax_fc_age"])
        opt.load_state_dict(dict_checkpoint["state_optimizer"])
        lr_scheduler.load_state_dict(dict_checkpoint["state_lr_scheduler"])
        del dict_checkpoint

    for key, value in cfg.items():
        num_space = 25 - len(key)
        logging.info(": " + key + " " * num_space + str(value))

    callback_verification = CallBackVerification(
        val_targets=cfg.val_targets, rec_prefix=cfg.rec, 
        summary_writer=summary_writer, wandb_logger = wandb_logger,
        cfg=cfg
    )
    callback_logging = CallBackLogging(
        frequent=cfg.frequent,
        num_epoch=cfg.num_epoch,
        total_step=cfg.total_step,
        batch_size=cfg.batch_size,
        start_step = global_step,
        writer=summary_writer
    )

    loss_id_am = AverageMeter()
    loss_age_am = AverageMeter()
    loss_gender_am = AverageMeter()
    loss_race_am = AverageMeter()
    loss_total_am = AverageMeter()
    amp = torch.cuda.amp.grad_scaler.GradScaler(growth_interval=100)

    for epoch in range(start_epoch, cfg.num_epoch):

        if isinstance(train_loader, DataLoader):
            train_loader.sampler.set_epoch(epoch)
        # for _, (img, local_labels) in enumerate(train_loader):
        for _, train_batch in enumerate(train_loader):
            if len(train_batch) == 2:
                img, local_labels = train_batch
            elif len(train_batch) == 4:
                img, local_labels, race_labels, gender_labels = train_batch
            elif len(train_batch) == 5:
                img, local_labels, age_labels, gender_labels, race_labels = train_batch

            global_step += 1
            
            local_embeddings = backbone(img)

            # undesired_indices = (race_labels != -1).nonzero().squeeze()   # because the dataset Casia-Webface doesn't have race and gender labels
            local_embeddings_normalized = torch.unsqueeze(torch.nn.functional.normalize(local_embeddings, dim=1), 1)
            # age_embeddings = backbone_age(local_embeddings_normalized)
            # gender_embeddings = backbone_gender(local_embeddings_normalized)
            # race_embeddings = backbone_race(local_embeddings_normalized)
            # print('discrim_embeddings.size():', discrim_embeddings.size())
            # print('race_labels.size():', race_labels.size())

            loss_id: torch.Tensor = module_partial_fc(local_embeddings, local_labels)
            loss_age: torch.Tensor = module_partial_fc_age(local_embeddings, age_labels)
            loss_gender: torch.Tensor = module_partial_fc_gender(local_embeddings, gender_labels)
            loss_race: torch.Tensor = module_partial_fc_race(local_embeddings, race_labels)
            # loss_total: torch.Tensor = loss_id - (alfa * loss_discrim)   # adversarial learning
            loss_total: torch.Tensor = loss_id + loss_race + loss_age + loss_gender   # collaborative learning

            if cfg.fp16:
                # amp.scale(loss_id).backward()
                # amp.scale(loss_discrim).backward()   # Bernardo
                amp.scale(loss_total).backward()       # Bernardo
                if global_step % cfg.gradient_acc == 0:
                    amp.unscale_(opt)
                    torch.nn.utils.clip_grad_norm_(backbone.parameters(), 5)
                    # torch.nn.utils.clip_grad_norm_(backbone_race.parameters(), 5)
                    amp.step(opt)
                    amp.update()
                    opt.zero_grad()
            else:
                # loss_id.backward()
                # loss_discrim.backward()   # Bernardo
                loss_total.backward()       # Bernardo
                if global_step % cfg.gradient_acc == 0:
                    torch.nn.utils.clip_grad_norm_(backbone.parameters(), 5)
                    # torch.nn.utils.clip_grad_norm_(backbone_race.parameters(), 5)
                    opt.step()
                    opt.zero_grad()
            lr_scheduler.step()

            with torch.no_grad():
                if wandb_logger:
                    wandb_logger.log({
                        'Loss/Step LossID': loss_id.item(),
                        'Loss/Step LossDISCRIM': loss_race.item(),
                        'Loss/Step LossTOTAL': loss_total.item(),
                        # 'Loss/Train Loss': loss_id_am.avg,
                        'Process/Step': global_step,
                        'Process/Epoch': epoch
                    })

                loss_id_am.update(loss_id.item(), 1)
                loss_age_am.update(loss_age.item(), 1)                                                       # Bernardo
                loss_gender_am.update(loss_gender.item(), 1)                                                       # Bernardo
                loss_race_am.update(loss_race.item(), 1)                                                       # Bernardo
                loss_total_am.update(loss_total.item(), 1)                                                           # Bernardo
                callback_logging('id', global_step, loss_id_am, epoch, cfg.fp16, lr_scheduler.get_last_lr()[0], amp)
                callback_logging('age', global_step, loss_age_am, epoch, cfg.fp16, lr_scheduler.get_last_lr()[0], amp)  # Bernardo
                callback_logging('gender', global_step, loss_gender_am, epoch, cfg.fp16, lr_scheduler.get_last_lr()[0], amp)  # Bernardo
                callback_logging('race', global_step, loss_race_am, epoch, cfg.fp16, lr_scheduler.get_last_lr()[0], amp)  # Bernardo
                callback_logging('total', global_step, loss_total_am, epoch, cfg.fp16, lr_scheduler.get_last_lr()[0], amp)    # Bernardo

                # if global_step % cfg.verbose == 0 and global_step > 0:
                if global_step % cfg.verbose == 0:
                    callback_verification(global_step, backbone)

        if cfg.save_all_states:
            checkpoint = {
                "epoch": epoch + 1,
                "global_step": global_step,
                "state_dict_backbone": backbone.module.state_dict(),
                # "state_dict_backbone_age": backbone_age.module.state_dict(),
                # "state_dict_backbone_gender": backbone_gender.module.state_dict(),
                # "state_dict_backbone_race": backbone_race.module.state_dict(),
                "state_dict_softmax_fc": module_partial_fc.state_dict(),
                "state_dict_softmax_fc_age": module_partial_fc_age.state_dict(),
                "state_dict_softmax_fc_gender": module_partial_fc_gender.state_dict(),
                "state_dict_softmax_fc_race": module_partial_fc_race.state_dict(),
                "state_optimizer": opt.state_dict(),
                "state_lr_scheduler": lr_scheduler.state_dict()
            }
            torch.save(checkpoint, os.path.join(cfg.output, f"checkpoint_gpu_{rank}.pt"))

        if rank == 0:
            path_module = os.path.join(cfg.output, "model.pt")
            torch.save(backbone.module.state_dict(), path_module)

            if wandb_logger and cfg.save_artifacts:
                artifact_name = f"{run_name}_E{epoch}"
                model = wandb.Artifact(artifact_name, type='model')
                model.add_file(path_module)
                wandb_logger.log_artifact(model)
                
        if cfg.dali:
            train_loader.reset()

    if rank == 0:
        path_module = os.path.join(cfg.output, "model.pt")
        torch.save(backbone.module.state_dict(), path_module)
        
        if wandb_logger and cfg.save_artifacts:
            artifact_name = f"{run_name}_Final"
            model = wandb.Artifact(artifact_name, type='model')
            model.add_file(path_module)
            wandb_logger.log_artifact(model)



if __name__ == "__main__":
    torch.backends.cudnn.benchmark = True
    parser = argparse.ArgumentParser(
        description="Distributed Arcface Training in Pytorch")
    parser.add_argument("config", type=str, help="py config file")
    parser.add_argument("--resume", default="", type=str, help="work_dirs/idiffface-uniform_frcsyn_r100/2024-03-30_00-30-26_GPU0_MULTI-TASK-AGE-GENDER-RACE/model.pt")
    parser.add_argument("--annotation", default="", type=str, help="py config file")
    main(parser.parse_args())
