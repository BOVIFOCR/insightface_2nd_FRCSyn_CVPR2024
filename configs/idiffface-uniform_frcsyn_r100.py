from easydict import EasyDict as edict
import os
uname = os.uname()

# make training faster
# our RAM is 256G
# mount -t tmpfs -o size=140G  tmpfs /train_tmp

config = edict()
config.margin_list = (1.0, 0.5, 0.0)
config.network = "r100"
config.resume = False
config.output = None
config.embedding_size = 512
config.sample_rate = 1.0
config.fp16 = True
config.momentum = 0.9
config.weight_decay = 5e-4
config.batch_size = 128
# config.batch_size = 64
# config.batch_size = 32
config.lr = 0.1
config.verbose = 2000
# config.verbose = 10
config.dali = False


config.loss = 'CombinedMarginLoss'   # default
# config.loss = 'LDAMLoss'
# config.loss = 'FocalLoss'

config.train_rule = None             # default
# config.train_rule = 'Resample'
# config.train_rule = 'Reweight'
# config.train_rule = 'DRW'



if uname.nodename == 'duo':
    # config.rec = "/train_tmp/faces_emore"
    config.rec = ['/datasets1/bjgbiesseck/SDFR_at_FG2024/datasets/synthetic/IDiff-Face_ICCV2023/ca-cpd25-synthetic-uniform-10050']      # duo

    # config.val_targets = ['']
    config.val_targets = ['/datasets2/1st_frcsyn_wacv2024/datasets/real/1_CASIA-WebFace/faces_webface_112x112/lfw.bin', '/datasets2/1st_frcsyn_wacv2024/datasets/real/1_CASIA-WebFace/faces_webface_112x112/calfw.bin', '/datasets2/1st_frcsyn_wacv2024/datasets/real/1_CASIA-WebFace/faces_webface_112x112/cplfw.bin', '/datasets2/1st_frcsyn_wacv2024/datasets/real/1_CASIA-WebFace/faces_webface_112x112/agedb_30.bin', '/datasets2/1st_frcsyn_wacv2024/datasets/real/1_CASIA-WebFace/faces_webface_112x112/cfp_fp.bin', 'bupt']
    config.val_dataset_dir = ['/datasets2/1st_frcsyn_wacv2024/datasets/real/3_BUPT-BalancedFace/race_per_7000_crops_112x112']
    config.val_protocol_path = ['/datasets2/2nd_frcsyn_cvpr2024/comparison_files/comparison_files_2/sub-tasks_1.1_1.2_1.3/bupt_comparison.txt']


elif uname.nodename == 'daugman':
    config.rec = ['/groups/unico/SDFR_at_FG2024/datasets/synthetic/IDiff-Face_ICCV2023/ca-cpd25-synthetic-uniform-10050']

    # config.val_targets = ['']
    config.val_targets = ['/groups/unico/1st_frcsyn_wacv2024/datasets/real/1_CASIA-WebFace/faces_webface_112x112/lfw.bin', '/groups/unico/1st_frcsyn_wacv2024/datasets/real/1_CASIA-WebFace/faces_webface_112x112/calfw.bin', '/groups/unico/1st_frcsyn_wacv2024/datasets/real/1_CASIA-WebFace/faces_webface_112x112/cplfw.bin', '/groups/unico/1st_frcsyn_wacv2024/datasets/real/1_CASIA-WebFace/faces_webface_112x112/agedb_30.bin', '/groups/unico/1st_frcsyn_wacv2024/datasets/real/1_CASIA-WebFace/faces_webface_112x112/cfp_fp.bin', 'bupt']
    config.val_dataset_dir = ['/groups/unico/1st_frcsyn_wacv2024/datasets/real/3_BUPT-BalancedFace/race_per_7000_crops_112x112']
    config.val_protocol_path = ['/groups/unico/2nd_frcsyn_cvpr2024/comparison_files/comparison_files_2/sub-tasks_1.1_1.2_1.3/bupt_comparison.txt']


# elif uname.nodename == 'diolkos':
#     config.rec = '/nobackup/unico/SDFR_at_FG2024/datasets/synthetic/DigiFace-1M_500Kimg_100Kids_5imgs-id/images'   # diolkos
#
#     config.val_targets = ['/home/bjgbiesseck/datasets/1st_frcsyn_wacv2024/faces_webface_112x112/lfw.bin', '/home/bjgbiesseck/datasets/1st_frcsyn_wacv2024/faces_webface_112x112/cfp_fp.bin', '/home/bjgbiesseck/datasets/1st_frcsyn_wacv2024/faces_webface_112x112/agedb_30.bin', 'bupt']
#     # config.val_targets = ['bupt']
#     config.val_dataset_dir = ['/nobackup/unico/1st_frcsyn_wacv2024/datasets/real/3_BUPT-BalancedFace/race_per_7000_crops_112x112']
#     config.val_protocol_path = ['/nobackup/unico/1st_frcsyn_wacv2024/comparison_files/comparison_files/sub-tasks_1.1_1.2/bupt_comparison.txt']

else:
    raise Exception(f'Paths of train and val datasets could not be found in file \'{__file__}\'')


# config.num_classes = 85742
config.num_classes = 10049

# config.num_image = 5822653
config.num_image = 502450

# config.num_epoch = 20
config.num_epoch = 30
config.warmup_epoch = 0



# WandB Logger
# config.wandb_key = "XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
config.wandb_key = "d7664714c72bd594a957381812da450279f80f66"

config.suffix_run_name = None

config.using_wandb = False
# config.using_wandb = True

# config.wandb_entity = "entity"
config.wandb_entity = "bovifocr"

config.wandb_project = "R100_DCFace_10000classes_whole_dataset"
config.wandb_log_all = True

# config.save_artifacts = False
config.save_artifacts = True

config.wandb_resume = False # resume wandb run: Only if the you wand t resume the last run that it was interrupted

config.notes = ''
