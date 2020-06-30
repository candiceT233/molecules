import os
import time
import torch
import matplotlib
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D
import numpy as np
from sklearn.manifold import TSNE
from .callback import Callback

# TODO: add numba, tensorboard, Pillow dependency to conda env
import numba
@numba.jit
def minmax(x):
    max_, min_ = x[0], x[0]
    for i in x[1:]:
        if i > max_:
            max_ = i
        elif i < min_:
            min_ = i
    return min_, max_

class EmbeddingCallback(Callback):
    """
    Saves VAE embeddings of random samples.

    Parameters
    ----------
    data : torch.Tensor
        Dataset from which to sample for embeddings.

    """
    def __init__(self, data, directory, rmsd=None, writer=None):

        os.makedirs(directory, exist_ok=True)
        # TODO: put data to_device
        self.data = data
        self.directory = directory
        self.writer = writer

        if rmsd is not None:
            self._init_plot(rmsd)

    def _init_plot(self, rmsd):
        self.fig = plt.figure()
        self.ax = self.fig.add_subplot(111, projection='3d')

        cmi = plt.get_cmap('jet')
        cnorm = matplotlib.colors.Normalize(vmin=min(rmsd), vmax=max(rmsd))
        scalar_map = matplotlib.cm.ScalarMappable(norm=cnorm, cmap=cmi)
        scalar_map.set_array(rmsd)
        self.fig.colorbar(scalar_map)

        self.color = scalar_map.to_rgba(rmsd)

    def on_train_begin(self, logs):
        self.embeddings = []
        self.data_index = []

    def on_epoch_end(self, epoch, logs):
        # TODO: may need to change the torch device
        idx = torch.randint(len(self.data), (1,))
        embedding = logs['model'].encode(self.data[idx])
        self.data_index.append(idx)
        self.embeddings.append(embedding)

        if hasattr(self, 'fig'):
            self.tsne_plot(logs)

    def tsne_plot(self, logs):
        # Outputs 3D embeddings using all available processors
        tsne = TSNE(n_components=3, n_jobs=-1)
        embeddings = tsne.fit_transform(logs['model'].encode(self.data)) # TODO: convert to np?

        z1, z2, z3 = embeddings[:, 0], embeddings[:, 1], embeddings[:, 2]

        self.ax.scatter3D(z1, z2, z3, marker='.', c=self.color)
        self.ax.set_xlim3d(minmax(z1))
        self.ax.set_ylim3d(minmax(z2))
        self.ax.set_zlim3d(minmax(z3))
        self.ax.set_xlabel(r'$z_1$')
        self.ax.set_ylabel(r'$z_2$')
        self.ax.set_zlabel(r'$z_3$')
        self.ax.set_title(f'RMSD to native state after epoch {logs["global_step"]}')
        time_stamp = time.strftime(f'epoch-{logs["global_step"]}-%Y%m%d-%H%M%S.png')
        plt.savefig(os.path.join(self.directory, time_stamp), dpi=300)
        if self.writer is not None:
            self.writer.add_figure('epoch t-SNE embeddings', self.fig, logs['global_step'])
        self.ax.clear()

    def save(self, path):
        """
        Save embeddings and index of associated data point.

        Parameters
        ----------
        path: str
            Path to save embeddings and indices

        """
        torch.save({'embeddings': self.embeddings, 'indices': self.data_index}, path)
