import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import tpae.data.samples as tds
import tpae.data.patchcollection as tdp
import tpae.training as tt
import tpae.association as ta
import tpae.vis as tv
from tpaesim import synthesize
import tpaesim.cc
import gc, os
import cv2 as cv2
import xarray as xr
import argparse
import os.path

import torch

def make_trivial(P, modelfilename, n_epochs):
    P.augmentation_off()
    P.numpy_mode()

    Zs = {}
    Zs['trivial-avg'] = P[:][0][:,:,:,:].mean(axis=(1,2))
    Zs['trivial-pixels'] = P[:][0].reshape((len(P), -1))
    Zs['trivial-cov'] = np.array([z.T.dot(z)[np.triu_indices(P.nchannels)]
        for z in P[:][0].reshape((len(P), -1, P.nchannels))])

    return Zs

def make_simplecnn(P, modelfilename, n_epochs, kl_weight, stem):
    print(kl_weight, stem)
    from tpae.models.simple_vae import SimpleVAE
    modelparams = {
        'ncolors':P.nchannels,
        'patch_size':patchsize,
        'nfilters1':512,
        'nfilters2':1024,
    }
    model = SimpleVAE(**modelparams)
    if os.path.isfile(modelfilename):
        model.load_state_dict(torch.load(modelfilename))
    else:
        train_dataset, val_dataset = tt.train_test_split(P)
        optimizer = torch.optim.AdamW(model.parameters(), lr=1e-4)
        scheduler = torch.optim.lr_scheduler.ExponentialLR(optimizer, gamma=0.9)
        model, losslogs = tt.full_training(model, train_dataset, val_dataset, optimizer, scheduler, batch_size=256, n_epochs=n_epochs,
                                        kl_weight=kl_weight, kl_warmup=True)
        torch.save(model.state_dict(), modelfilename)

    return {f'simplecnn{stem}-latent': ta.apply(model, P)}

def make_resnetsimple(P, modelfilename, n_epochs, kl_weight, stem):
    print(kl_weight, stem)
    from tpae.models.resnet_vae import ResnetVAE
    model = ResnetVAE(len(P.meta.sid.unique()), network='light', mode='simple', ncolors=P.nchannels)
    if os.path.isfile(modelfilename):
        model.load_state_dict(torch.load(modelfilename))
    else:
        train_dataset, val_dataset = tt.train_test_split(P)
        optimizer = torch.optim.AdamW(model.parameters(), lr=1e-2)
        scheduler = torch.optim.lr_scheduler.ExponentialLR(optimizer, gamma=0.9)
        model, losslogs = tt.full_training(model, train_dataset, val_dataset, optimizer, scheduler, batch_size=256, n_epochs=n_epochs,
                                        kl_weight=kl_weight, kl_warmup=True)
        torch.save(model.state_dict(), modelfilename)

    Zs = {}
    Z = ta.apply(model, P, embedding=model.penultimate_layer)
    Zs[f'resnetsimple{stem}'] = Z.reshape((len(Z), -1))

    return Zs

def make_resnetadvanced(P, modelfilename, n_epochs, kl_weight, stem):
    print(kl_weight, stem)
    from tpae.models.resnet_vae import ResnetVAE
    model = ResnetVAE(len(P.meta.sid.unique()), network='light', mode='advanced', ncolors=P.nchannels)
    if os.path.isfile(modelfilename):
        model.load_state_dict(torch.load(modelfilename))
    else:
        train_dataset, val_dataset = tt.train_test_split(P)
        optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3)
        scheduler = torch.optim.lr_scheduler.ExponentialLR(optimizer, gamma=0.9)
        model, losslogs = tt.full_training(model, train_dataset, val_dataset, optimizer, scheduler, batch_size=256, n_epochs=n_epochs,
                                        kl_weight=kl_weight, kl_warmup=True)
        torch.save(model.state_dict(), modelfilename)

    Zs = {}
    Zs[f'resnetadvanced{stem}'] = ta.apply(model, P, embedding=model.embedding)

    return Zs

signal_adders = {
    'agg_v_nothing' : synthesize.add_aggregates_v_nothing,
    'agg_v_diffuse' : synthesize.add_aggregates_v_diffuse,
    'linear_v_circular' : synthesize.add_linear_v_circular
}

kls = [1e-8, 1e-7, 1e-6, 1e-5, 1e-4, 1e-3, 1e-2, 1e-1, 1, 10]
rep_makers = {
    'trivial' : make_trivial } | \
    { f'simplecnn_kl{i+1}' : lambda *x, i=i, j=j: make_simplecnn(*x, j, f'kl{i+1}')
        for i, j in enumerate(kls) } | \
    { f'resnetsimple_kl{i+1}' : lambda *x, i=i, j=j: make_resnetsimple(*x, j, f'kl{i+1}')
        for i, j in enumerate(kls) } | \
    { f'resnetadv_kl{i+1}' : lambda *x, i=i, j=j: make_resnetadvanced(*x, j, f'kl{i+1}')
        for i, j in enumerate(kls) }

if __name__ == "__main__":
    # Initialize the parser
    parser = argparse.ArgumentParser(
        description="perform single simulation replicate for a specified family of methods")

    # Add arguments
    parser.add_argument("signal_type", type=str, help="The signal type (string)")
    parser.add_argument("rep_family", type=str, help="The rep family (string)")
    parser.add_argument("seed", type=int, help="The seed (integer)")
    parser.add_argument("outdir", type=str, help="Output directory")
    parser.add_argument("-d", action='store_true')
    parser.add_argument("--data-dir", type=str, help="Path to data", default="/data/srlab1/yakir/ST/ALZ/alz-data/10u/pca_k=10_harmony")
    parser.add_argument("--npcs", type=int, help="number of PCs to use for building UMAP; if none then no PCA used.", default=-1)
    parser.add_argument("--torch-device", type=str, help="Device to send pytorch tensors to. mps for Apple.", default=None)

    # Parse the arguments
    args = parser.parse_args()
    print(args)
    if args.torch_device is not None:
        torch.set_default_device(args.torch_device)

    if args.d:
        stop_after=15; max_frac_empty=0.3; n_epochs=1
    else:
        stop_after=None; max_frac_empty=0.8; n_epochs=10
    outstem = f'{args.outdir}/{args.signal_type}.{args.rep_family}.{args.seed}'

    # Read data
    print('reading samples')
    samples = tds.read_samples(f'{args.data_dir}/*.nc', tds.default_parser, stop_after=stop_after)
    gc.collect()

    # Spike in signal
    print('adding in case/ctrl signal')
    np.random.seed(args.seed)
    samples, samplemeta, region_masks = signal_adders[args.signal_type](samples, plot=False)
    gc.collect()

    # Generate patches
    print('choosing patches')
    patchsize = 40; patchstride = 10
    patchmeta = tds.choose_patches(samples, patchsize, patchstride, max_frac_empty=max_frac_empty)
    synthesize.annotate_patches(patchmeta, region_masks)
    P = tdp.PatchCollection(patchmeta, samples, standardize=True)
    print(len(patchmeta), 'patches generated')
    del samples
    gc.collect()

    # Generate representations
    print('making representations')
    Zs = rep_makers[args.rep_family](
        P,
        f'{outstem}.model.pt',
        n_epochs)
    gc.collect()

    # Generate anndata
    print('making anndata objects')
    kwargs = {'use_rep':'X_pca', 'n_comps':args.npcs} if args.npcs != -1 else {'use_rep':'X'}
    suffix = f'-{args.npcs}pcs' if args.npcs != -1 else '-raw'
    Ds = {
        repname+suffix : ta.anndata(P.meta, Z, samplemeta, sampleid='sid', **kwargs)
        for repname, Z in Zs.items()
        }

    # Perform two case-control anlayses for each representation and noise level
    results = pd.DataFrame(columns=['signal', 'repname', 'style', 'noise', 'P'] + tpaesim.cc.metric_names())
    noises = [0, 0.1, 0.2, 0.3, 0.4, 0.5]

    if os.path.isfile(f'{outstem}.tsv'):
        results = pd.read_csv(f'{outstem}.tsv', sep='\t')
    for repname, D in Ds.items():
        for noise in noises:
            D.samplem['noisy_case'] = (D.samplem.case + np.random.binomial(1, noise, size=D.N)) % 2
            for style, cc in [
                    ('clust', tpaesim.cc.cluster_cc),
                    ('cna', tpaesim.cc.cna_cc),
                    ]:
                p, metrics = cc(D)
                results.loc[len(results)] = {
                    "signal": args.signal_type,
                    "repname": repname,
                    "style": style,
                    "noise": noise,
                    "P": p,
                    **metrics
                    }
                print(results.iloc[-1])
                gc.collect()
                
                # Write output as tsv with repname, p, accuracy
                results.to_csv(f'{outstem}.tsv', sep='\t', index=False)
