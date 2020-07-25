import torch
import numpy as np
from torch.utils.data import Dataset
from molecules.utils import open_h5

class ContactMapDataset(Dataset):
    """
    PyTorch Dataset class to load contact matrix data. Uses HDF5
    files and only reads into memory what is necessary for one batch.
    """
    def __init__(self, path, split_ptc=0.8, split='train',
                 squeeze=False, sparse=False, gpu=None):
        """
        Parameters
        ----------
        path : str
            Path to h5 file containing contact matrices.

        split_ptc : float
            Percentage of total data to be used as training set.

        split : str
            Either 'train' or 'valid', specifies whether this
            dataset returns train or validation data.

        squeeze : bool
            If True, data is reshaped to (H, W) else if False data
            is reshaped to (1, H, W).

        sparse : bool
            If True, process data as sparse row/col COO format. Data
            should not contain any values because they are all 1's and
            generated on the fly. If False, input data is normal tensor.

        gpu : int, None
            If None, then data will be put onto the default GPU if CUDA
            is available and otherwise is put onto a CPU. If gpu is int
            type, then data is put onto the specified GPU.
        """
        if split not in ('train', 'valid'):
            raise ValueError("Parameter split must be 'train' or 'valid'.")
        if split_ptc < 0 or split_ptc > 1:
            raise ValueError('Parameter split_ptc must satisfy 0 <= split_ptc <= 1.')

        # Open h5 file. Python's garbage collector closes the
        # file when class is destructed.
        h5_file = open_h5(path)

        if sparse:
            group = h5_file['contact_maps']
            self.row_dset = group.get('row')
            self.col_dset = group.get('col')
        else:
            # contact_maps dset has shape (N, W, H, 1)
            self.dset = h5_file['contact_maps']
 
        # train validation split index
        self.split_ind = int(split_ptc * len(self.dset))
        self.split = split
        self.sparse = sparse

        if squeeze:
            self.shape = (self.dset.shape[1], self.dset.shape[2])
        else:
            self.shape = (1, self.dset.shape[1], self.dset.shape[2])

        if gpu is None:
            self.device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        else:
            self.device = torch.device(f'cuda:{gpu}')

    def __len__(self):
        if self.split == 'train':
            return self.split_ind
        return len(self.dset) - self.split_ind

    def __getitem__(self, idx):
        if self.split == 'valid':
            idx += self.split_ind

        if self.sparse:
            indices = torch.from_numpy(np.vstack((self.row_dset[idx],
                                                  self.col_dset[idx]))) \
                                      .to(self.device).to(torch.long)
            values = torch.ones(indices.shape[1], dtype=torch.float32,
                                device=self.device)
            data = torch.sparse.FloatTensor(indices, values).to_dense()
        else:
            data = torch.from_numpy(np.array(self.dset[idx]))

        return data.view(self.shape).to(self.device).to(torch.float32)

