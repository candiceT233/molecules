import os
import time
import click
from os.path import join

# torch stuff
from torchsummary import summary
import torch
import torch.distributed as dist
from torch.nn.parallel import DistributedDataParallel as DDP
from torch.utils.data import DataLoader, Subset

# mpi4py
import mpi4py
mpi4py.rc.initialize = False
from mpi4py import MPI

# molecules stuff
from molecules.ml.datasets import PointCloudDataset
from molecules.ml.hyperparams import OptimizerHyperparams
from molecules.ml.callbacks import (LossCallback, CheckpointCallback,
                                    SaveEmbeddingsCallback, TSNEPlotCallback)
from molecules.ml.unsupervised.point_autoencoder import AAE3d, AAE3dHyperparams

# hpo stuff
import ray
from ray import tune
from hyperopt import hp
from ray.tune.suggest.hyperopt import HyperOptSearch
from ray.tune.integration.wandb import wandb_mixin
from ray.tune.integration.wandb import WandbLogger

# parser
def parse_dict(ctx, param, value):
    if value is not None:
        token = value.split(",")
        result = {}
        for item in token:
            k, v = item.split("=")
            result[k] = v
        return result

@click.command()
@click.option('-i', '--input', 'input_path', required=True,
              type=click.Path(exists=True),
              help='Path to file containing preprocessed contact matrix data')

@click.option('-dn', '--dataset_name', required=True, type=str,
              help='Name of the dataset in the HDF5 file.')

@click.option('-rn', '--rmsd_name', required=True, type=str,
              help='Name of the RMSD data in the HDF5 file.')

@click.option('-o', '--out', 'out_path', required=True,
              type=click.Path(exists=True),
              help='Output directory for model data')

@click.option('-m', '--model_prefix', required=True, type=str,
              help='Model Prefix in for file naming. The current time will be appended')

@click.option('-np', '--num_points', required=True, type=int,
              help='number of input points')

@click.option('-nf', '--num_features', default=1, type=int,
              help='number of features per point in addition to 3D coordinates')

@click.option('-E', '--encoder_gpu', default=None, type=int,
              help='Encoder GPU id')

@click.option('-G', '--generator_gpu', default=None, type=int,
              help='Generator GPU id')

@click.option('-D', '--discriminator_gpu', default=None, type=int,
              help='Discriminator GPU id')

@click.option('-e', '--epochs', default=10, type=int,
              help='Number of epochs to train for')

@click.option('-lw', '--loss_weights', callback=parse_dict,
              help='Loss parameters')

@click.option('-S', '--sample_interval', default=20, type=int,
              help="For embedding plots. Plots every sample_interval'th point")

@click.option('--local_rank', default=None, type=int,
              help='Local rank on the machine, required for DDP')

@click.option('-wp', '--wandb_project_name', default=None, type=str,
              help='Project name for wandb logging')

@click.option('-wp', '--wandb_api_key', default=None, type=str,
              help='API key for wandb logging')

@click.option('--distributed', is_flag=True,
              help='Enable distributed training')

def main(input_path, dataset_name, rmsd_name,
         out_path, model_prefix,
         num_points, num_features,
         encoder_gpu, generator_gpu, discriminator_gpu,
         epochs, loss_weights, sample_interval, local_rank,
         wandb_api_key, wandb_project_name, distributed):
         """Example for training Fs-peptide with AAE3d."""
         
         # init raytune
         ray.init()
         
         tune_config = {
             "input_path": input_path,
             "dataset_name": dataset_name,
             "rmsd_name": rmsd_name,
             "out_path": out_path,
             "checkpoint": None,
             "model_prefix": model_prefix,
             "num_points": num_points,
             "num_features": num_features,
             "encoder_gpu": encoder_gpu,
             "generator_gpu": generator_gpu,
             "discriminator_gpu": discriminator_gpu,
             "epochs": epochs,
             "batch_size": hp.choice("batch_size", [4, 8, 16, 32]),
             "optimizer": {
                 "name": "Adam", 
                 "lr": hp.loguniform("lr", 1e-5, 1e-1)
             },
             "latent_dim": hp.choice("latent_dim", [64, 128, 256]),
             "loss_weights": {key: float(loss_weights[key]) for key in loss_weights},
             "encoder_kernel_sizes": hp.choice("encoder_kernel_sizes", 
                 [[3, 3, 1, 1, 1], 
                 [5, 3, 3, 1, 1],
                 [5, 5, 3, 1, 1],
                 [5, 5, 3, 3, 1]]),
             "noise_std": hp.choice("noise_std", [0.2, 0.5, 1.0]),
             "sample_interval": sample_interval,
             "distributed": distributed,
             "local_rank": local_rank,
             "wandb": {
                 "project": wandb_project_name,
                 "api_key": wandb_api_key
             }
         }
         
         tune_config_good = tune_config.copy()
         # we need to feed the indices here for choice args
         tune_config_good["batch_size"] = 3
         tune_config_good["optimizer"]["lr"] = 1e-4
         tune_config_good["latent_dim"] = 2
         tune_config_good["encoder_kernel_sizes"] = 2
         tune_config_good["noise_std"] = 0
         
         hyperopt_search = HyperOptSearch(tune_config, points_to_evaluate = [tune_config_good],
             max_concurrent=1, metric="loss_eg", mode="min")

         analysis = tune.run(run_config, 
                         loggers=[WandbLogger], 
                         resources_per_trial={'gpu': 1}, 
                         num_samples=20, 
                         search_alg=hyperopt_search)
         
         # goodbye
         ray.shutdown()

# main function, called by raytune
@wandb_mixin
def run_config(config):
    """Example for training Fs-peptide with AAE3d."""

    # get parameters from config
    input_path = config["input_path"]
    dataset_name = config["dataset_name"]
    rmsd_name = config["rmsd_name"]
    out_path = config["out_path"]
    checkpoint = config["checkpoint"]
    model_prefix = config["model_prefix"]
    num_points = config["num_points"]
    num_features = config["num_features"]
    encoder_gpu = config["encoder_gpu"]
    generator_gpu = config["generator_gpu"]
    discriminator_gpu = config["discriminator_gpu"]
    epochs = config["epochs"]
    batch_size = config["batch_size"]
    optimizer = config["optimizer"]
    latent_dim = config["latent_dim"]
    loss_weights = config["loss_weights"]
    sample_interval = config["sample_interval"]
    wandb_project_name = config["wandb"]["project"]
    wandb_api_key = config["wandb"]["api_key"]
    distributed = config["distributed"]
    local_rank = config["local_rank"]
    encoder_kernel_sizes = config["encoder_kernel_sizes"]
    noise_std = config["noise_std"]
    
    # use this as unique identifier
    model_id = time.strftime(f"{model_prefix}-%Y%m%d-%H%M%S")

    # do some scaffolding for DDP
    comm_rank = 0
    comm_size = 1
    comm_local_rank = 0
    comm = None
    if distributed and dist.is_available():
        # init mpi4py:
        MPI.Init_thread()

        # get communicator: duplicate from comm world
        comm = MPI.COMM_WORLD.Dup()

        # now match ranks between the mpi comm and the nccl comm
        os.environ["WORLD_SIZE"] = str(comm.Get_size())
        os.environ["RANK"] = str(comm.Get_rank())

        # init pytorch
        dist.init_process_group(backend='nccl',
                                init_method='env://')
        comm_rank = dist.get_rank()
        comm_size = dist.get_world_size()
        if local_rank is not None:
            comm_local_rank = local_rank
        else:
            comm_local_rank = int(os.getenv("LOCAL_RANK", 0))
    
    # HP
    # model
    aae_hparams = {
        "num_features": num_features,
        "latent_dim": latent_dim,
        "encoder_kernel_sizes": encoder_kernel_sizes,
        "noise_std": noise_std,
        "lambda_rec": float(loss_weights["lambda_rec"]),
        "lambda_gp": float(loss_weights["lambda_gp"])
        }
    hparams = AAE3dHyperparams(**aae_hparams)
    
    # optimizers
    optimizer_hparams = OptimizerHyperparams(name = optimizer["name"],
                                             hparams={'lr':float(optimizer["lr"])})

    aae = AAE3d(num_points, num_features, batch_size, hparams, optimizer_hparams,
              gpu=(encoder_gpu, generator_gpu, discriminator_gpu))

    if comm_size > 1:
        if (encoder_gpu == decoder_gpu):
            devid = torch.device(f'cuda:{encoder_gpu}')
            aae.model = DDP(aae.model, device_ids = [devid], output_device = devid)
        else:
            aae.model = DDP(aae.model, device_ids = None, output_device = None)
    
    if comm_rank == 0:
        # Diplay model 
        print(aae)
    
        # Only print summary when encoder_gpu is None or 0
        #summary(aae.model, (3 + num_features, num_points))

    # Load training and validation data
    train_dataset = PointCloudDataset(input_path,
                                      dataset_name,
                                      rmsd_name,
                                      num_points,
                                      num_features,
                                      split = 'train',
                                      normalize = 'box',
                                      cms_transform = False)

    # split across nodes
    if comm_size > 1:
        chunksize = len(train_dataset) // comm_size
        train_dataset = Subset(train_dataset,
                               list(range(chunksize * comm_rank, chunksize * (comm_rank + 1))))
    
    train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle = True, drop_last = True,
                              pin_memory = True, num_workers = 1)

    valid_dataset = PointCloudDataset(input_path,
                                      dataset_name,
                                      rmsd_name,
                                      num_points,
                                      num_features,
                                      split = 'valid',
                                      normalize = 'box',
                                      cms_transform = False)

    # split across nodes
    if comm_size > 1:
        chunksize = len(valid_dataset) // comm_size
        valid_dataset = Subset(valid_dataset,
                               list(range(chunksize * comm_rank, chunksize * (comm_rank + 1))))
    
    valid_loader = DataLoader(valid_dataset, batch_size=batch_size, shuffle = True, drop_last = True,
                              pin_memory = True, num_workers = 1)

    print(f"Having {len(train_dataset)} training and {len(valid_dataset)} validation samples.")
    
    # For ease of training multiple models
    model_path = join(out_path, f'model-{model_id}')

    # do we want wandb
    wandb_config = None
    if (comm_rank == 0) and (wandb_project_name is not None):
        import wandb
        wandb.login(key = wandb_api_key)
        wandb.init(project = wandb_project_name,
                   name = model_id,
                   id = model_id,
                   dir = join(out_path, "wandb_cache"),
                   resume = False,
                   config=config)
        wandb_config = wandb.config
        
        # watch model
        wandb.watch(aae.model)
    
    # Optional callbacks
    loss_callback = LossCallback(join(model_path, 'loss.json'),
                                 wandb_config=wandb_config,
                                 mpi_comm=comm)
    
    checkpoint_callback = CheckpointCallback(out_dir=join(model_path, 'checkpoint'),
                                             mpi_comm=comm)

    save_callback = SaveEmbeddingsCallback(out_dir=join(model_path, 'embedddings'),
                                           interval=embed_interval,
                                           sample_interval=sample_interval,
                                           mpi_comm=comm)

    # TSNEPlotCallback requires SaveEmbeddingsCallback to run first
    tsne_callback = TSNEPlotCallback(out_dir=join(model_path, 'embedddings'),
                                     projection_type='3d',
                                     target_perplexity=100,
                                     colors=['rmsd', 'fnc'],
                                     interval=tsne_interval,
                                     wandb_config=wandb_config,
                                     mpi_comm=comm)

    # Train model with callbacks
    callbacks = [loss_callback, checkpoint_callback, save_callback, tsne_callback]


    # train model with callbacks
    aae.train(train_loader, valid_loader, epochs,
              checkpoint = checkpoint,
              callbacks = callbacks)

    # Save loss history to disk.
    if comm_rank == 0:
        loss_callback.save(join(model_path, 'loss.json'))

        # Save hparams to disk
        hparams.save(join(model_path, 'model-hparams.json'))
        optimizer_hparams.save(join(model_path, 'optimizer-hparams.json'))

        # Save final model weights to disk
        aae.save_weights(join(model_path, 'encoder-weights.pt'),
                         join(model_path, 'generator-weights.pt'),
                         join(model_path, 'discriminator-weights.pt'))

    # prepare return dict
    final_losses = {key: loss_callback.valid_losses[key][-1] for key in loss_callback.valid_losses}

    return final_losses

    # Output directory structure
    #  out_path
    # ├── model_path
    # │   ├── checkpoint
    # │   │   ├── epoch-1-20200606-125334.pt
    # │   │   └── epoch-2-20200606-125338.pt
    # │   ├── decoder-weights.pt
    # │   ├── encoder-weights.pt
    # │   ├── loss.json
    # │   ├── model-hparams.pkl
    # │   └── optimizer-hparams.pkl
    
if __name__ == '__main__':
    main()
