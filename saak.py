'''
@ author: Jiali Duan
@ function: Saak Transform
@ Date: 10/29/2017
@ To do: parallelization
'''

# load libs
import torch
import argparse
from torchvision import datasets, transforms
import matplotlib.pyplot as plt
import numpy as np
from data.datasets import MNIST
import torch.utils.data as data_utils
from sklearn.decomposition import PCA
import torch.nn.functional as F
from torch.autograd import Variable
from itertools import product
import math

# argument parsing
print(torch.__version__)

batch_size=1
test_batch_size=1
kwargs={}

class PrintHelper(object):
    SHOULD_PRINT = False
    @staticmethod
    def print(s):
        if PrintHelper.SHOULD_PRINT:
            print(s)


# show sample
def show_sample(inv):
    inv_img=inv.data.numpy()[0][0]
    plt.imshow(inv_img)
    plt.gray()
    plt.savefig('./image/demo.png')
   # plt.show()

'''
@ For demo use, only extracts the first 1000 samples
'''
def create_numpy_dataset(num_images, train_loader):
    datasets = []
    if num_images is None:
        num_images = len(train_loader)
    for i, data in enumerate(train_loader):
        data_numpy = data[0].numpy()
        data_numpy = np.squeeze(data_numpy)
        datasets.append(data_numpy)
        if i==(num_images-1):
            break
    datasets = np.array(datasets)
    if len(datasets.shape)==3: # the input image is grayscale image
        datasets = np.expand_dims(datasets, axis=1)
    return datasets



'''
@ data: flatten patch data: (14*14*60000,1,2,2)
@ return: augmented anchors
'''
def PCA_and_augment(data_in, energy_thresh=1.0):
    # data reshape
    data=np.reshape(data_in,(data_in.shape[0],-1))
    PrintHelper.print('PCA_and_augment data shape:{}'.format(data.shape))
    # patch mean removal
    mean = np.mean(data, axis=1, keepdims=True)
    data_mean_remov = data - mean

    # PCA, retain all components
    if energy_thresh == 1.0:
        pca = PCA(svd_solver='full')
    else:
        pca = PCA(n_components=energy_thresh, svd_solver='full')
    ## if 0 < n_components < 1 and svd_solver == 'full'
    # select the number of components such that the amount of variance that needs to be explained
    # is greater than the percentage specified by n_components

    pca.fit(data_mean_remov)
    # idx = pca.explained_variance_ratio_> 0.03
    # comps=pca.components_[idx,:]
    comps = pca.components_
    feat_mean = pca.mean_

    # augment
    if comps.shape[0] == data.shape[1]:
        PrintHelper.print('all comps are kept')
        ac_comps = comps[:-1] #if all comps are kept, the last comp is dc anchor vec, don't augment
    else:
        ac_comps = comps
    comps_neg=[vec*(-1) for vec in ac_comps]
    comps_complete=np.vstack((ac_comps, comps_neg))
    PrintHelper.print('PCA_and_augment comps_complete shape: {}'.format(comps_complete.shape))
    return comps_complete, feat_mean



'''
@ datasets: numpy data as input
@ depth: determine shape, initial: 0
'''

def fit_pca_shape(datasets,depth, start_dim = 32):
    factor=np.power(2,depth)
    length=int(start_dim/factor)
    PrintHelper.print('fit_pca_shape: length: {}'.format(length))
    idx1=range(0,length,2)
    idx2=[i+2 for i in idx1]
    PrintHelper.print('fit_pca_shape: idx1: {}'.format(idx1))
    data_lattice=[datasets[:,:,i:j,k:l] for ((i,j),(k,l)) in product(zip(idx1,idx2),zip(idx1,idx2))]
    data_lattice=np.array(data_lattice)
    PrintHelper.print('fit_pca_shape: data_lattice.shape: {}'.format(data_lattice.shape))
    data=np.reshape(data_lattice,(data_lattice.shape[0]*data_lattice.shape[1],data_lattice.shape[2],2,2))
    PrintHelper.print('fit_pca_shape: reshape: {}'.format(data.shape))
    return data


'''
@ Prepare shape changes.
@ return filters and datasets for convolution
@ aug_anchors: [7,4] -> [7,input_channel,2,2]
@ feat_shape: [4] -> [input_channel,2,2]
@ output_datas: [60000*num_patch*num_patch,channel,2,2]

'''
def ret_filt_patches(aug_anchors, feat_mean):
    input_channel=int(aug_anchors.shape[1]/4)
    num=aug_anchors.shape[0]
    filt=np.reshape(aug_anchors,(num,input_channel,4))

    # reshape to kernels, (7,shape,2,2)
    filters=np.reshape(filt,(num,input_channel,2,2))
    threeD_mean = np.reshape(feat_mean, (input_channel,2,2))

    # reshape datasets, (60000*shape*shape,shape,28,28)
    # datasets=np.expand_dims(dataset,axis=1)

    return filters, threeD_mean



'''
@ input: numpy kernel and data
@ output: conv+relu result
'''
def conv_and_relu(filters, datasets, stride=2):
    # torch data change
    filters_t=torch.from_numpy(filters)
    datasets_t=torch.from_numpy(datasets)

    # Variables
    filt=Variable(filters_t).type(torch.FloatTensor)
    data=Variable(datasets_t).type(torch.FloatTensor)

    # Convolution
    output=F.conv2d(data,filt,stride=stride)

    # Relu
    relu_output=F.relu(output)

    return relu_output

def conv(filters,datasets,stride=2):
    # torch data change
    filters_t=torch.from_numpy(filters)
    datasets_t=torch.from_numpy(datasets)

    # Variables
    filt=Variable(filters_t).type(torch.FloatTensor)
    data=Variable(datasets_t).type(torch.FloatTensor)

    # Convolution
    output=F.conv2d(data,filt,stride=stride)

    return output



'''
@ One-stage Saak transform
@ input: datasets [60000, channel, size,size]
'''
def one_stage_saak_trans(datasets=None, stage=0, energy_thresh=1.0,
        start_dim=32):
    # load dataset, (60000,1,32,32)
    # input_channel: 1->7
    PrintHelper.print('one_stage_saak_trans: datasets.shape {}'.format(datasets.shape))
    input_channels=datasets.shape[1]

    # change data shape, (14*60000,4)
    data_flatten=fit_pca_shape(datasets,stage, start_dim)

    # augmented components
    comps_complete, feat_mean = PCA_and_augment(data_flatten, energy_thresh=energy_thresh)
    PrintHelper.print('one_stage_saak_trans: comps_complete: {}'.format(comps_complete.shape))

    # get filter and data, (6,1,2,2) (60000,1,32,32)
    filters, threeD_mean = ret_filt_patches(comps_complete, feat_mean)
    PrintHelper.print('one_stage_saak_trans: filters: {}'.format(filters.shape))

    #subtract patch mean
    N, C, H, W = datasets.shape

    mean_filter = 1.0 / (C* 2 * 2) * np.ones((1, C, 2, 2), dtype=np.float32)
    patch_mean = conv(mean_filter, datasets, stride=2)
    patch_mean_up = F.upsample(patch_mean, scale_factor=2, mode='nearest')

    normalized_data = datasets - patch_mean_up.data.numpy()

    #subtract feature mean before pca transform
    tiled_mean = np.tile(threeD_mean, (1, int(H/2), int(W/2)))
    normalized_data -= np.expand_dims(tiled_mean, axis=0)

    # output (60000,6,14,14)
    relu_output = conv_and_relu(filters,normalized_data,stride=2)

    ac_feature = relu_output.data.numpy()

    #add dc back
    dc = patch_mean.data.numpy() * np.sqrt(C* 2 * 2)
    output = np.concatenate((ac_feature, dc),axis=1)

    PrintHelper.print('one_stage_saak_trans: output: {}'.format(output.shape))
    return filters,threeD_mean,output

'''
@ Testing One-stage Saak transform
@ input: datasets [60000, channel, size,size]
'''

def test_one_stage_saak_trans(test_data, feat_mean, filters):


    # load dataset, (60000,1,32,32)
    # input_channel: 1->7
    PrintHelper.print('one_stage_saak_trans: test_data.shape {}'.format(test_data.shape))

    #subtract patch mean
    N, C, H, W = test_data.shape
    mean_filter = 1.0 / (C* 2 * 2) * np.ones((1, C, 2, 2), dtype=np.float32)
    patch_mean = conv(mean_filter, test_data, stride=2)
    patch_mean_up = F.upsample(patch_mean, scale_factor=2, mode='nearest')

    normalized_data = test_data - patch_mean_up.data.numpy()

    #subtract feature mean before pca transform
    tiled_mean = np.tile(feat_mean, (1, int(H/2), int(W/2)))
    normalized_data -= np.expand_dims(tiled_mean, axis=0)

    # output (60000,6,14,14)
    relu_output = conv_and_relu(filters,normalized_data,stride=2)

    ac_feature = relu_output.data.numpy()

    #add dc back
    dc = patch_mean.data.numpy() * np.sqrt(C* 2 * 2)
    output = np.concatenate((ac_feature, dc),axis=1)

    PrintHelper.print('one_stage_saak_trans: output: {}'.format(output.shape))
    return output



'''
@ Multi-stage Saak transform
'''
def multi_stage_saak_trans(data, energy_thresh=1.0):
    filters = []
    outputs = []
    means = []
    spatial_extent=data.shape[-1]
    num_stages = int(math.log(spatial_extent, 2))

    # num_stages=0
    # while(spatial_extent>=2):
    #     num_stages+=1
    #     spatial_extent/=2

    for i in range(num_stages):
        PrintHelper.print('{} stage of saak transform: '.format(i))
        filt,mean,data=one_stage_saak_trans(data, stage=i,
                energy_thresh=energy_thresh, start_dim=spatial_extent)
        filters.append(filt)
        outputs.append(data)
        means.append(mean)
        PrintHelper.print('')


    return filters, means, outputs

def test_multi_stage_saak_trans(test_data, feat_means, filters):
    num_stages = len(filters)
    outputs = []
    for i in range(num_stages):
        PrintHelper.print('{} stage of saak transform: '.format(i))
        test_data=test_one_stage_saak_trans(test_data, feat_means[i], filters[i])
        outputs.append(test_data)
        PrintHelper.print('')
    return outputs

'''
@ Reconstruction from the second last stage
@ In fact, reconstruction can be done from any stage
'''
def toy_recon(outputs,filters):
    outputs=outputs[::-1][1:]
    filters=filters[::-1][1:]
    num=len(outputs)
    data=outputs[0]
    for i in range(num):
        data = F.conv_transpose2d(data, filters[i], stride=2)

    return data

'''
P/S conversion to get useful feature
'''
def p_s_conversion(position_feature):
    n, c, h, w = position_feature.shape
    dc_feat = np.expand_dims(position_feature[:, -1, :, :], axis=1)
    signed_ac_feat = position_feature[:, :int(c/2), :, :] - position_feature[:, int(c/2):-1, :, :]
    signed_feat = np.concatenate((dc_feat, signed_ac_feat), axis=1)
    return signed_feat

'''
flatten and concat saak features from all stages (stage 1-5)
input: list of output cuboids of multi-stage saak transform
'''
def get_final_feature(outputs):
    final_feature = None
    for output in outputs:
        signed_output = p_s_conversion(output)
        flattened_feat = np.reshape(signed_output, [signed_output.shape[0], -1])
        if final_feature is None:
            final_feature = flattened_feat
        else:
            final_feature = np.concatenate((final_feature, flattened_feat), axis=1)
    return final_feature


if __name__=='__main__':
    # Testing
    batch_size = 1
    test_batch_size = 1
    kwargs = {}
    train_loader = data_utils.DataLoader(MNIST(root='./data', train=True, process=False, transform=transforms.Compose([
        # transforms.Scale((32, 32)),
        transforms.Pad(2),
        transforms.ToTensor(),
    ])), batch_size=batch_size, shuffle=True, **kwargs)

    test_loader = data_utils.DataLoader(MNIST(root='./data', train=False, process=False, transform=transforms.Compose([
        # transforms.Scale((32, 32)),
        transforms.Pad(2),
        transforms.ToTensor(),
    ])), batch_size=test_batch_size, shuffle=True, **kwargs)
    num_images = 2000
    data = create_numpy_dataset(num_images, train_loader)
    filters, means, outputs = multi_stage_saak_trans(data, energy_thresh=0.97)
    final_feat_dim = sum([(((output.shape[1]-1)/2+1)*output.shape[2]*output.shape[3]) for output in outputs])
    final_feat = get_final_feature(outputs)
    assert final_feat.shape[1] == final_feat_dim
    PrintHelper.print('final feature dimension is {}'.format(final_feat_dim))

    PrintHelper.print('\n-----------------start testing-------------\n')

    test_data = create_numpy_dataset(num_images/2, test_loader)
    test_outputs = test_multi_stage_saak_trans(test_data, means, filters)
    test_final_feat = get_final_feature(test_outputs)
    assert test_final_feat.shape[1] == final_feat_dim
    PrintHelper.print(test_final_feat.shape)







