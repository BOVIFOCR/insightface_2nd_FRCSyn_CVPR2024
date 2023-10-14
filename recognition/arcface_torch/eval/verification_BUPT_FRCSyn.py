"""Helper for evaluation on the Labeled Faces in the Wild dataset 
"""

# MIT License
#
# Copyright (c) 2016 David Sandberg
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.


import datetime
import os, sys
import pickle

import torch
import mxnet as mx
import numpy as np
import sklearn
import torch
from mxnet import ndarray as nd
from scipy import interpolate
from sklearn.decomposition import PCA
from sklearn.model_selection import KFold

sys.path.insert(0, "../")
from backbones import get_model

import argparse   # Bernardo
import itertools

from loader_BUPT import Loader_BUPT


class LFold:
    def __init__(self, n_splits=2, shuffle=False):
        self.n_splits = n_splits
        if self.n_splits > 1:
            self.k_fold = KFold(n_splits=n_splits, shuffle=shuffle)

    def split(self, indices):
        if self.n_splits > 1:
            return self.k_fold.split(indices)
        else:
            return [(indices, indices)]


def calculate_roc(thresholds,
                  embeddings1,
                  embeddings2,
                  actual_issame,
                  nrof_folds=10,
                  pca=0):
    assert (embeddings1.shape[0] == embeddings2.shape[0])
    assert (embeddings1.shape[1] == embeddings2.shape[1])
    nrof_pairs = min(len(actual_issame), embeddings1.shape[0])
    nrof_thresholds = len(thresholds)
    k_fold = LFold(n_splits=nrof_folds, shuffle=False)

    tprs = np.zeros((nrof_folds, nrof_thresholds))
    fprs = np.zeros((nrof_folds, nrof_thresholds))
    accuracy = np.zeros((nrof_folds))
    indices = np.arange(nrof_pairs)

    if pca == 0:
        diff = np.subtract(embeddings1, embeddings2)
        dist = np.sum(np.square(diff), 1)

    for fold_idx, (train_set, test_set) in enumerate(k_fold.split(indices)):
        if pca > 0:
            print('doing pca on', fold_idx)
            embed1_train = embeddings1[train_set]
            embed2_train = embeddings2[train_set]
            _embed_train = np.concatenate((embed1_train, embed2_train), axis=0)
            pca_model = PCA(n_components=pca)
            pca_model.fit(_embed_train)
            embed1 = pca_model.transform(embeddings1)
            embed2 = pca_model.transform(embeddings2)
            embed1 = sklearn.preprocessing.normalize(embed1)
            embed2 = sklearn.preprocessing.normalize(embed2)
            diff = np.subtract(embed1, embed2)
            dist = np.sum(np.square(diff), 1)

        # Find the best threshold for the fold
        acc_train = np.zeros((nrof_thresholds))
        for threshold_idx, threshold in enumerate(thresholds):
            _, _, acc_train[threshold_idx] = calculate_accuracy(
                threshold, dist[train_set], actual_issame[train_set])
        best_threshold_index = np.argmax(acc_train)
        for threshold_idx, threshold in enumerate(thresholds):
            tprs[fold_idx, threshold_idx], fprs[fold_idx, threshold_idx], _ = calculate_accuracy(
                threshold, dist[test_set],
                actual_issame[test_set])
        _, _, accuracy[fold_idx] = calculate_accuracy(
            thresholds[best_threshold_index], dist[test_set],
            actual_issame[test_set])

    tpr = np.mean(tprs, 0)
    fpr = np.mean(fprs, 0)
    return tpr, fpr, accuracy


def calculate_accuracy(threshold, dist, actual_issame):
    predict_issame = np.less(dist, threshold)
    tp = np.sum(np.logical_and(predict_issame, actual_issame))
    fp = np.sum(np.logical_and(predict_issame, np.logical_not(actual_issame)))
    tn = np.sum(
        np.logical_and(np.logical_not(predict_issame),
                       np.logical_not(actual_issame)))
    fn = np.sum(np.logical_and(np.logical_not(predict_issame), actual_issame))

    tpr = 0 if (tp + fn == 0) else float(tp) / float(tp + fn)
    fpr = 0 if (fp + tn == 0) else float(fp) / float(fp + tn)
    acc = float(tp + tn) / dist.size
    return tpr, fpr, acc


def calculate_val(thresholds,
                  embeddings1,
                  embeddings2,
                  actual_issame,
                  far_target,
                  nrof_folds=10):
    assert (embeddings1.shape[0] == embeddings2.shape[0])
    assert (embeddings1.shape[1] == embeddings2.shape[1])
    nrof_pairs = min(len(actual_issame), embeddings1.shape[0])
    nrof_thresholds = len(thresholds)
    k_fold = LFold(n_splits=nrof_folds, shuffle=False)

    val = np.zeros(nrof_folds)
    far = np.zeros(nrof_folds)

    diff = np.subtract(embeddings1, embeddings2)
    dist = np.sum(np.square(diff), 1)
    indices = np.arange(nrof_pairs)

    for fold_idx, (train_set, test_set) in enumerate(k_fold.split(indices)):

        # Find the threshold that gives FAR = far_target
        far_train = np.zeros(nrof_thresholds)
        for threshold_idx, threshold in enumerate(thresholds):
            _, far_train[threshold_idx] = calculate_val_far(
                threshold, dist[train_set], actual_issame[train_set])
        if np.max(far_train) >= far_target:
            f = interpolate.interp1d(far_train, thresholds, kind='slinear')
            threshold = f(far_target)
        else:
            threshold = 0.0

        val[fold_idx], far[fold_idx] = calculate_val_far(
            threshold, dist[test_set], actual_issame[test_set])

    val_mean = np.mean(val)
    far_mean = np.mean(far)
    val_std = np.std(val)
    return val_mean, val_std, far_mean


def calculate_val_far(threshold, dist, actual_issame):
    predict_issame = np.less(dist, threshold)
    true_accept = np.sum(np.logical_and(predict_issame, actual_issame))
    false_accept = np.sum(
        np.logical_and(predict_issame, np.logical_not(actual_issame)))
    n_same = np.sum(actual_issame)
    n_diff = np.sum(np.logical_not(actual_issame))
    # print(true_accept, false_accept)
    # print(n_same, n_diff)
    val = float(true_accept) / float(n_same)
    far = float(false_accept) / float(n_diff)
    return val, far


def evaluate(embeddings, actual_issame, nrof_folds=10, pca=0):
    # Calculate evaluation metrics
    thresholds = np.arange(0, 4, 0.01)
    embeddings1 = embeddings[0::2]
    embeddings2 = embeddings[1::2]
    tpr, fpr, accuracy = calculate_roc(thresholds,
                                       embeddings1,
                                       embeddings2,
                                       np.asarray(actual_issame),
                                       nrof_folds=nrof_folds,
                                       pca=pca)
    thresholds = np.arange(0, 4, 0.001)
    val, val_std, far = calculate_val(thresholds,
                                      embeddings1,
                                      embeddings2,
                                      np.asarray(actual_issame),
                                      1e-3,
                                      nrof_folds=nrof_folds)
    return tpr, fpr, accuracy, val, val_std, far


@torch.no_grad()
def load_bin(path, image_size):
    try:
        with open(path, 'rb') as f:
            bins, issame_list = pickle.load(f)  # py2
    except UnicodeDecodeError as e:
        with open(path, 'rb') as f:
            bins, issame_list = pickle.load(f, encoding='bytes')  # py3
    data_list = []
    for flip in [0, 1]:
        data = torch.empty((len(issame_list) * 2, 3, image_size[0], image_size[1]))
        data_list.append(data)
    for idx in range(len(issame_list) * 2):
        _bin = bins[idx]
        img = mx.image.imdecode(_bin)
        if img.shape[1] != image_size[0]:
            img = mx.image.resize_short(img, image_size[0])
        img = nd.transpose(img, axes=(2, 0, 1))
        for flip in [0, 1]:
            if flip == 1:
                img = mx.ndarray.flip(data=img, axis=2)
            data_list[flip][idx][:] = torch.from_numpy(img.asnumpy())
        if idx % 1000 == 0:
            print('loading bin', idx)
    print(data_list[0].shape)
    return data_list, issame_list


@torch.no_grad()
def test(data_set, backbone, batch_size, nfolds=10):
    data_list = data_set[0]
    issame_list = data_set[1]
    embeddings_list = []
    time_consumed = 0.0
    for i in range(len(data_list)):
        data = data_list[i]
        embeddings = None
        ba = 0
        while ba < data.shape[0]:
            bb = min(ba + batch_size, data.shape[0])
            count = bb - ba
            _data = data[bb - batch_size: bb]
            time0 = datetime.datetime.now()
            img = ((_data / 255) - 0.5) / 0.5
            # print('img:', img)
            # print('img.size():', img.size())

            net_out: torch.Tensor = backbone(img)         # original
            # net_out: torch.Tensor = backbone.forward(img)   # Bernardo

            _embeddings = net_out.detach().cpu().numpy()
            time_now = datetime.datetime.now()
            diff = time_now - time0
            time_consumed += diff.total_seconds()
            if embeddings is None:
                embeddings = np.zeros((data.shape[0], _embeddings.shape[1]))
            embeddings[ba:bb, :] = _embeddings[(batch_size - count):, :]
            ba = bb
        embeddings_list.append(embeddings)

    _xnorm = 0.0
    _xnorm_cnt = 0
    for embed in embeddings_list:
        for i in range(embed.shape[0]):
            _em = embed[i]
            _norm = np.linalg.norm(_em)
            _xnorm += _norm
            _xnorm_cnt += 1
    _xnorm /= _xnorm_cnt

    embeddings = embeddings_list[0].copy()
    embeddings = sklearn.preprocessing.normalize(embeddings)
    acc1 = 0.0
    std1 = 0.0
    embeddings = embeddings_list[0] + embeddings_list[1]
    embeddings = sklearn.preprocessing.normalize(embeddings)
    print(embeddings.shape)
    print('infer time', time_consumed)
    _, _, accuracy, val, val_std, far = evaluate(embeddings, issame_list, nrof_folds=nfolds)
    acc2, std2 = np.mean(accuracy), np.std(accuracy)
    return acc1, std1, acc2, std2, _xnorm, embeddings_list







###################################################
# RACES ANALYSIS (African, Asian, Caucasian, Indian)
###################################################

def get_races_combinations():
    races = ['African', 'Asian', 'Caucasian', 'Indian']
    races_comb = [(race, race) for race in races]
    # races_comb += list(itertools.combinations(races, 2))
    return sorted(races_comb)


def get_avg_roc_metrics_races(metrics_races=[{}], races_combs=[]):
    avg_roc_metrics = {}
    for i, race_comb in enumerate(races_combs):
        accs = [metrics_races[fold_idx][race_comb]['acc'] for fold_idx in range(len(metrics_races))]
        tprs = [metrics_races[fold_idx][race_comb]['tpr'] for fold_idx in range(len(metrics_races))]
        fprs = [metrics_races[fold_idx][race_comb]['fpr'] for fold_idx in range(len(metrics_races))]

        avg_roc_metrics[race_comb] = {}
        avg_roc_metrics[race_comb]['acc_mean'] = np.mean(accs)
        avg_roc_metrics[race_comb]['acc_std']  = np.std(accs)
        avg_roc_metrics[race_comb]['tpr_mean'] = np.mean(tprs)
        avg_roc_metrics[race_comb]['tpr_std']  = np.std(tprs)
        avg_roc_metrics[race_comb]['fpr_mean'] = np.mean(fprs)
        avg_roc_metrics[race_comb]['fpr_std']  = np.std(fprs)
    return avg_roc_metrics


def get_avg_val_metrics_races(metrics_races=[{}], races_combs=[]):
    avg_val_metrics = {}
    for i, race_comb in enumerate(races_combs):
        vals = [metrics_races[fold_idx][race_comb]['val'] for fold_idx in range(len(metrics_races))]
        fars = [metrics_races[fold_idx][race_comb]['far'] for fold_idx in range(len(metrics_races))]

        avg_val_metrics[race_comb] = {}
        avg_val_metrics[race_comb]['val_mean'] = np.mean(vals)
        avg_val_metrics[race_comb]['val_std']  = np.std(vals)
        avg_val_metrics[race_comb]['far_mean'] = np.mean(fars)
        avg_val_metrics[race_comb]['far_std']  = np.std(fars)
    return avg_val_metrics



def calculate_roc_analyze_races(thresholds,
                  embeddings1,
                  embeddings2,
                  actual_issame,
                  races_list,
                  subj_list,
                  nrof_folds=10,
                  pca=0,
                  races_combs=[]):
    assert (embeddings1.shape[0] == embeddings2.shape[0])
    assert (embeddings1.shape[1] == embeddings2.shape[1])
    nrof_pairs = min(len(actual_issame), embeddings1.shape[0])
    nrof_thresholds = len(thresholds)
    k_fold = LFold(n_splits=nrof_folds, shuffle=False)

    tprs = np.zeros((nrof_folds, nrof_thresholds))
    fprs = np.zeros((nrof_folds, nrof_thresholds))
    accuracy = np.zeros((nrof_folds))
    indices = np.arange(nrof_pairs)
    metrics_races = [None] * nrof_folds

    if pca == 0:
        diff = np.subtract(embeddings1, embeddings2)
        dist = np.sum(np.square(diff), 1)

    for fold_idx, (train_set, test_set) in enumerate(k_fold.split(indices)):
        if pca > 0:
            print('doing pca on', fold_idx)
            embed1_train = embeddings1[train_set]
            embed2_train = embeddings2[train_set]
            _embed_train = np.concatenate((embed1_train, embed2_train), axis=0)
            pca_model = PCA(n_components=pca)
            pca_model.fit(_embed_train)
            embed1 = pca_model.transform(embeddings1)
            embed2 = pca_model.transform(embeddings2)
            embed1 = sklearn.preprocessing.normalize(embed1)
            embed2 = sklearn.preprocessing.normalize(embed2)
            diff = np.subtract(embed1, embed2)
            dist = np.sum(np.square(diff), 1)

        # Find the best threshold for the fold
        acc_train = np.zeros((nrof_thresholds))
        for threshold_idx, threshold in enumerate(thresholds):
            _, _, acc_train[threshold_idx] = calculate_accuracy_analyze_races(
                threshold, dist[train_set], actual_issame[train_set], races_list=None, subj_list=None, races_combs=None)
        best_threshold_index = np.argmax(acc_train)
        for threshold_idx, threshold in enumerate(thresholds):
            tprs[fold_idx, threshold_idx], fprs[fold_idx, threshold_idx], _ = calculate_accuracy_analyze_races(
                threshold, dist[test_set], actual_issame[test_set], races_list=None, subj_list=None, races_combs=None)
        _, _, accuracy[fold_idx], metrics_races[fold_idx] = calculate_accuracy_analyze_races(
            thresholds[best_threshold_index], dist[test_set],
            actual_issame[test_set], races_list[test_set], subj_list[test_set], races_combs=races_combs)

    avg_roc_metrics = get_avg_roc_metrics_races(metrics_races, races_combs)
    # for i, race_comb in enumerate(races_combs):
    #     print(f'avg_roc_metrics[{race_comb}][\'acc_mean\']:', avg_roc_metrics[race_comb]['acc_mean'], '+-', avg_roc_metrics[race_comb]['acc_std'])
    #     print(f'avg_roc_metrics[{race_comb}][\'tpr_mean\']:', avg_roc_metrics[race_comb]['tpr_mean'], '+-', avg_roc_metrics[race_comb]['tpr_std'])
    #     print(f'avg_roc_metrics[{race_comb}][\'fpr_mean\']:', avg_roc_metrics[race_comb]['fpr_mean'], '+-', avg_roc_metrics[race_comb]['fpr_std'])
    # sys.exit(0)

    tpr = np.mean(tprs, 0)
    fpr = np.mean(fprs, 0)
    return tpr, fpr, accuracy, avg_roc_metrics


def calculate_accuracy_analyze_races(threshold, dist, actual_issame, races_list, subj_list, races_combs):
    predict_issame = np.less(dist, threshold)
    tp = np.sum(np.logical_and(predict_issame, actual_issame))
    fp = np.sum(np.logical_and(predict_issame, np.logical_not(actual_issame)))
    tn = np.sum(np.logical_and(np.logical_not(predict_issame), np.logical_not(actual_issame)))
    fn = np.sum(np.logical_and(np.logical_not(predict_issame), actual_issame))

    tpr = 0 if (tp + fn == 0) else float(tp) / float(tp + fn)
    fpr = 0 if (fp + tn == 0) else float(fp) / float(fp + tn)
    acc = float(tp + tn) / dist.size

    # race analysis (African, Asian, Caucasian, Indian)
    if not races_list is None and not subj_list is None:
        metrics_races = {}
        for race_comb in races_combs:
            metrics_races[race_comb] = {}

        for i, race_comb in enumerate(races_combs):
            indices_race_comb = np.where(np.all(races_list == race_comb, axis=1))[0]
            metrics_races[race_comb]['tp'] = np.sum(np.logical_and(predict_issame[indices_race_comb], actual_issame[indices_race_comb]))
            metrics_races[race_comb]['fp'] = np.sum(np.logical_and(predict_issame[indices_race_comb], np.logical_not(actual_issame[indices_race_comb])))
            metrics_races[race_comb]['tn'] = np.sum(np.logical_and(np.logical_not(predict_issame[indices_race_comb]), np.logical_not(actual_issame[indices_race_comb])))
            metrics_races[race_comb]['fn'] = np.sum(np.logical_and(np.logical_not(predict_issame[indices_race_comb]), actual_issame[indices_race_comb]))

            metrics_races[race_comb]['tpr'] = 0 if (metrics_races[race_comb]['tp'] + metrics_races[race_comb]['fn'] == 0) else float(metrics_races[race_comb]['tp']) / float(metrics_races[race_comb]['tp'] + metrics_races[race_comb]['fn'])
            metrics_races[race_comb]['fpr'] = 0 if (metrics_races[race_comb]['fp'] + metrics_races[race_comb]['tn'] == 0) else float(metrics_races[race_comb]['fp']) / float(metrics_races[race_comb]['fp'] + metrics_races[race_comb]['tn'])
            metrics_races[race_comb]['acc'] = 0 if indices_race_comb.size == 0 else float(metrics_races[race_comb]['tp'] + metrics_races[race_comb]['tn']) / indices_race_comb.size

    if races_list is None:
        return tpr, fpr, acc
    else:
        return tpr, fpr, acc, metrics_races



def calculate_val_analyze_races(thresholds,
                  embeddings1,
                  embeddings2,
                  actual_issame,
                  far_target,
                  races_list,
                  subj_list,
                  nrof_folds=10,
                  races_combs=[]):
    assert (embeddings1.shape[0] == embeddings2.shape[0])
    assert (embeddings1.shape[1] == embeddings2.shape[1])
    nrof_pairs = min(len(actual_issame), embeddings1.shape[0])
    nrof_thresholds = len(thresholds)
    k_fold = LFold(n_splits=nrof_folds, shuffle=False)

    val = np.zeros(nrof_folds)
    far = np.zeros(nrof_folds)

    diff = np.subtract(embeddings1, embeddings2)
    dist = np.sum(np.square(diff), 1)
    indices = np.arange(nrof_pairs)
    metrics_races = [None] * nrof_folds

    for fold_idx, (train_set, test_set) in enumerate(k_fold.split(indices)):

        # Find the threshold that gives FAR = far_target
        far_train = np.zeros(nrof_thresholds)
        for threshold_idx, threshold in enumerate(thresholds):
            _, far_train[threshold_idx] = calculate_val_far_analyze_races(
                threshold, dist[train_set], actual_issame[train_set], races_list=None, subj_list=None, races_combs=None)
        if np.max(far_train) >= far_target:
            f = interpolate.interp1d(far_train, thresholds, kind='slinear')
            threshold = f(far_target)
        else:
            threshold = 0.0

        val[fold_idx], far[fold_idx], metrics_races[fold_idx] = calculate_val_far_analyze_races(
            threshold, dist[test_set], actual_issame[test_set], races_list[test_set], subj_list[test_set], races_combs=races_combs)

    avg_val_metrics = get_avg_val_metrics_races(metrics_races, races_combs)

    val_mean = np.mean(val)
    far_mean = np.mean(far)
    val_std = np.std(val)
    return val_mean, val_std, far_mean, avg_val_metrics


def calculate_val_far_analyze_races(threshold, dist, actual_issame, races_list, subj_list, races_combs):
    predict_issame = np.less(dist, threshold)
    true_accept = np.sum(np.logical_and(predict_issame, actual_issame))
    false_accept = np.sum(np.logical_and(predict_issame, np.logical_not(actual_issame)))
    n_same = np.sum(actual_issame)
    n_diff = np.sum(np.logical_not(actual_issame))
    # print(true_accept, false_accept)
    # print(n_same, n_diff)
    val = float(true_accept) / float(n_same)
    far = float(false_accept) / float(n_diff)

    # race analysis (African, Asian, Caucasian, Indian)
    if not races_list is None and not subj_list is None:
        metrics_races = {}
        for race_comb in races_combs:
            metrics_races[race_comb] = {}

        for i, race_comb in enumerate(races_combs):
            indices_race_comb = np.where(np.all(races_list == race_comb, axis=1))[0]
            metrics_races[race_comb]['true_accept'] = np.sum(np.logical_and(predict_issame[indices_race_comb], actual_issame[indices_race_comb]))
            metrics_races[race_comb]['false_accept'] = np.sum(np.logical_and(predict_issame[indices_race_comb], np.logical_not(actual_issame[indices_race_comb])))
            metrics_races[race_comb]['n_same'] = np.sum(actual_issame[indices_race_comb])
            metrics_races[race_comb]['n_diff'] = np.sum(np.logical_not(actual_issame[indices_race_comb]))

            metrics_races[race_comb]['val'] = float(metrics_races[race_comb]['true_accept']) / float(metrics_races[race_comb]['n_same'])
            metrics_races[race_comb]['far'] = float(metrics_races[race_comb]['false_accept']) / float(metrics_races[race_comb]['n_diff'])
    
    if races_list is None:
        return val, far
    else:
        return val, far, metrics_races


def evaluate_analyze_races(embeddings, actual_issame, races_list, subj_list, nrof_folds=10, pca=0, races_combs=[]):
    # Calculate evaluation metrics
    thresholds = np.arange(0, 4, 0.01)
    embeddings1 = embeddings[0::2]
    embeddings2 = embeddings[1::2]
    tpr, fpr, accuracy, avg_roc_metrics = calculate_roc_analyze_races(thresholds,
                                                embeddings1,
                                                embeddings2,
                                                np.asarray(actual_issame),
                                                races_list,
                                                subj_list,
                                                nrof_folds=nrof_folds,
                                                pca=pca,
                                                races_combs=races_combs)
    thresholds = np.arange(0, 4, 0.001)
    val, val_std, far, avg_val_metrics = calculate_val_analyze_races(thresholds,
                                                embeddings1,
                                                embeddings2,
                                                np.asarray(actual_issame),
                                                1e-3,
                                                races_list,
                                                subj_list,
                                                nrof_folds=nrof_folds,
                                                races_combs=races_combs)
    return tpr, fpr, accuracy, val, val_std, far, avg_roc_metrics, avg_val_metrics


@torch.no_grad()
def test_analyze_races(args, data_set, backbone, batch_size, nfolds=10, races_combs=[]):
    data_list = data_set[0]
    issame_list = data_set[1]
    races_list = data_set[2]
    subj_list = data_set[3]

    path_embeddings = os.path.join(args.data_dir, 'embeddings_list.pkl')

    if not os.path.exists(path_embeddings) or not args.use_saved_embedd:
        print('\nComputing embeddings...')
        embeddings_list = []
        time_consumed = 0.0
        for i in range(len(data_list)):
            data = data_list[i]
            embeddings = None
            ba = 0
            while ba < data.shape[0]:
                bb = min(ba + batch_size, data.shape[0])
                print(f'{i+1}/{len(data_list)} - {bb}/{data.shape[0]}', end='\r')
                count = bb - ba
                _data = data[bb - batch_size: bb]
                time0 = datetime.datetime.now()
                img = ((_data / 255) - 0.5) / 0.5
                # print('img:', img)
                # print('img.size():', img.size())

                net_out: torch.Tensor = backbone(img)             # original
                # net_out: torch.Tensor = backbone.forward(img)   # Bernardo

                _embeddings = net_out.detach().cpu().numpy()
                time_now = datetime.datetime.now()
                diff = time_now - time0
                time_consumed += diff.total_seconds()
                if embeddings is None:
                    embeddings = np.zeros((data.shape[0], _embeddings.shape[1]))
                embeddings[ba:bb, :] = _embeddings[(batch_size - count):, :]
                ba = bb
            embeddings_list.append(embeddings)
            print('')
        print('infer time', time_consumed)
        
        print(f'Saving embeddings in file \'{path_embeddings}\' ...')
        write_object_to_file(path_embeddings, embeddings_list)
    else:
        print(f'Loading embeddings from file \'{path_embeddings}\' ...')
        embeddings_list = read_object_from_file(path_embeddings)

    print(f'Normalizing embeddings...')
    _xnorm = 0.0
    _xnorm_cnt = 0
    for embed in embeddings_list:
        for i in range(embed.shape[0]):
            _em = embed[i]
            _norm = np.linalg.norm(_em)
            _xnorm += _norm
            _xnorm_cnt += 1
    _xnorm /= _xnorm_cnt

    embeddings = embeddings_list[0].copy()
    embeddings = sklearn.preprocessing.normalize(embeddings)
    acc1 = 0.0
    std1 = 0.0
    embeddings = embeddings_list[0] + embeddings_list[1]
    embeddings = sklearn.preprocessing.normalize(embeddings)
    print(embeddings.shape)

    print('\nDoing races test evaluation...')
    # _, _, accuracy, val, val_std, far = evaluate(embeddings, issame_list, nrof_folds=nfolds)
    _, _, accuracy, val, val_std, far, avg_roc_metrics, avg_val_metrics = evaluate_analyze_races(embeddings, issame_list, races_list, subj_list, nrof_folds=nfolds, races_combs=races_combs)
    acc2, std2 = np.mean(accuracy), np.std(accuracy)
    return acc1, std1, acc2, std2, _xnorm, embeddings_list, val, val_std, far, avg_roc_metrics, avg_val_metrics


def read_object_from_file(path):
    with open(path, 'rb') as fid:
        any_obj = pickle.load(fid)
    return any_obj


def write_object_to_file(path, any_obj):
    with open(path, 'wb') as fid:
        pickle.dump(any_obj, fid)



def dumpR(data_set,
          backbone,
          batch_size,
          name='',
          data_extra=None,
          label_shape=None):
    print('dump verification embedding..')
    data_list = data_set[0]
    issame_list = data_set[1]
    embeddings_list = []
    time_consumed = 0.0
    for i in range(len(data_list)):
        data = data_list[i]
        embeddings = None
        ba = 0
        while ba < data.shape[0]:
            bb = min(ba + batch_size, data.shape[0])
            count = bb - ba

            _data = nd.slice_axis(data, axis=0, begin=bb - batch_size, end=bb)
            time0 = datetime.datetime.now()
            if data_extra is None:
                db = mx.io.DataBatch(data=(_data,), label=(_label,))
            else:
                db = mx.io.DataBatch(data=(_data, _data_extra),
                                     label=(_label,))
            model.forward(db, is_train=False)
            net_out = model.get_outputs()
            _embeddings = net_out[0].asnumpy()
            time_now = datetime.datetime.now()
            diff = time_now - time0
            time_consumed += diff.total_seconds()
            if embeddings is None:
                embeddings = np.zeros((data.shape[0], _embeddings.shape[1]))
            embeddings[ba:bb, :] = _embeddings[(batch_size - count):, :]
            ba = bb
        embeddings_list.append(embeddings)
    embeddings = embeddings_list[0] + embeddings_list[1]
    embeddings = sklearn.preprocessing.normalize(embeddings)
    actual_issame = np.asarray(issame_list)
    outname = os.path.join('temp.bin')
    with open(outname, 'wb') as f:
        pickle.dump((embeddings, issame_list),
                    f,
                    protocol=pickle.HIGHEST_PROTOCOL)


if __name__ == '__main__':

    parser = argparse.ArgumentParser(description='do verification')
    # general
    # parser.add_argument('--data-dir', default='', help='')                                                                                   # original
    # parser.add_argument('--data-dir', default='/datasets1/bjgbiesseck/MS-Celeb-1M/faces_emore', help='')                                     # Bernardo
    parser.add_argument('--data-dir', default='/datasets2/frcsyn_wacv2024/datasets/real/3_BUPT-BalancedFace/race_per_7000_112x112', help='')   # Bernardo

    parser.add_argument('--network', default='r100', type=str, help='')
    parser.add_argument('--model',
                        # default='../model/softmax,50',                                     # original
                        # default='../../model/model-r100-ii/model,0',                       # Bernardo
                        default='../trained_models/ms1mv3_arcface_r100_fp16/backbone.pth',   # Bernardo
                        help='path to load model.')
    parser.add_argument('--target',
                        # default='lfw,cfp_ff,cfp_fp,agedb_30',          # original
                        # default='lfw,cfp_fp,agedb_30',                 # original
                        # default='lfw',                                 # Bernardo
                        default='bupt',                                  # Bernardo
                        help='test targets.')
    parser.add_argument('--protocol', default='/datasets2/frcsyn_wacv2024/comparison_files/comparison_files/sub-tasks_1.1_1.2/bupt_comparison.txt', type=str, help='')
    parser.add_argument('--gpu', default=0, type=int, help='gpu id')
    parser.add_argument('--batch-size', default=32, type=int, help='')
    parser.add_argument('--max', default='', type=str, help='')
    parser.add_argument('--mode', default=0, type=int, help='')
    parser.add_argument('--nfolds', default=10, type=int, help='')
    parser.add_argument('--use-saved-embedd', action='store_true')
    args = parser.parse_args()


    image_size = [112, 112]
    print('image_size', image_size)

    ctx = mx.gpu(args.gpu)   # original
    # ctx = mx.cpu()         # Bernardo

    nets = []
    vec = args.model.split(',')
    prefix = args.model.split(',')[0]
    epochs = []

    # LOADING MODEL WITH PYTORCH
    nets = []
    time0 = datetime.datetime.now()
    print(f'Loading trained model \'{args.model}\'...')
    weight = torch.load(args.model)
    resnet = get_model(args.network, dropout=0, fp16=False).cuda()
    resnet.load_state_dict(weight)
    model = torch.nn.DataParallel(resnet)
    model.eval()
    nets.append(model)
    time_now = datetime.datetime.now()
    diff = time_now - time0
    print('model loading time', diff.total_seconds())


    ver_list = []
    ver_name_list = []
    for name in args.target.split(','):

        # Bernardo
        print('\ndataset name:', name)
        print('args.data_dir:', args.data_dir)

        path = os.path.join(args.data_dir, name + ".bin")
        if os.path.exists(path):
            print('loading.. ', name)
            data_set = load_bin(path, image_size)
            ver_list.append(data_set)
            ver_name_list.append(name)
            # sys.exit(0)
        
        else:
            if name.lower() == 'bupt':
                path_unified_dataset = os.path.join(args.data_dir, 'dataset.pkl')
                if not os.path.exists(path_unified_dataset):
                    print(f'Loading individual images from folder \'{args.data_dir}\' ...')
                    data_set = Loader_BUPT().load_dataset(args.protocol, args.data_dir, image_size)
                    print(f'Saving dataset in file \'{path_unified_dataset}\' ...')
                    write_object_to_file(path_unified_dataset, data_set)
                else:
                    print(f'Loading dataset from file \'{path_unified_dataset}\' ...')
                    data_set = read_object_from_file(path_unified_dataset)

                ver_list.append(data_set)
                ver_name_list.append(name)
                # print('data_set:', data_set)
                # sys.exit(0)
            else:
                raise Exception(f'Error, no \'.bin\' file found in \'{args.data_dir}\'')


    if args.mode == 0:
        for i in range(len(ver_list)):
            results = []
            for model in nets:
                if name.lower() == 'bupt':
                    races_combs = get_races_combinations()

                    acc1, std1, acc2, std2, xnorm, embeddings_list, val, val_std, far, avg_roc_metrics, avg_val_metrics = test_analyze_races(
                        args, ver_list[i], model, args.batch_size, args.nfolds, races_combs)
                    print('[%s]XNorm: %f' % (ver_name_list[i], xnorm))
                    # print('[%s]Accuracy: %1.5f+-%1.5f' % (ver_name_list[i], acc1, std1))
                    print('[%s]Accuracy-Flip: %1.5f+-%1.5f' % (ver_name_list[i], acc2, std2))
                    print('[%s]TAR: %1.5f+-%1.5f    FAR: %1.5f' % (ver_name_list[i], val, val_std, far))

                    for race_comb in races_combs:
                        race_comb_str = str((race_comb[0][:5], race_comb[1][:5]))
                        print('[%s]Acc %s: %1.5f+-%1.5f' % (ver_name_list[i], race_comb_str, avg_roc_metrics[race_comb]['acc_mean'], avg_roc_metrics[race_comb]['acc_std']), end='    ')
                        print('[%s]TAR %s: %1.5f+-%1.5f' % (ver_name_list[i], race_comb_str, avg_val_metrics[race_comb]['val_mean'], avg_val_metrics[race_comb]['val_std']), end='    ')
                        print('[%s]FAR %s: %1.5f+-%1.5f' % (ver_name_list[i], race_comb_str, avg_val_metrics[race_comb]['far_mean'], avg_val_metrics[race_comb]['far_std']))
                    results.append(acc2)

                else:
                    acc1, std1, acc2, std2, xnorm, embeddings_list = test(
                        ver_list[i], model, args.batch_size, args.nfolds)
                    print('[%s]XNorm: %f' % (ver_name_list[i], xnorm))
                    print('[%s]Accuracy: %1.5f+-%1.5f' % (ver_name_list[i], acc1, std1))
                    print('[%s]Accuracy-Flip: %1.5f+-%1.5f' % (ver_name_list[i], acc2, std2))
                    results.append(acc2)

            # print('Max of [%s] is %1.5f' % (ver_name_list[i], np.max(results)))
    elif args.mode == 1:
        raise ValueError
    else:
        model = nets[0]
        dumpR(ver_list[0], model, args.batch_size, args.target)
