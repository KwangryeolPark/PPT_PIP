import numpy as np
import warnings
import torch
import warnings
from multiprocessing import Pool
from typing import Union, Optional, Tuple
from .shuffler import PPT
from einops import rearrange
from tqdm.auto import tqdm

class ACF_COS(PPT):
    def __init__(
        self,
        channel_num: int,
        original_time_len: int,
        patch_len: int,
        permute_freq: int,
        permute_strategy: str = "random",
        permute_tensor_size: int = 1000,
        load_path: Optional[str] = None,
        device: Optional[Union[str, torch.device]] = None,
        seed: int = None,
        multiprocessing: bool = False,
        num_workers: int = 8,
    ):
        """
        AutoCorrelation Function Cosine Similarity (ACF-COS) class to quantify the Order of a time series.
        It inherits from the PPT class to use shuffling methods.

        Args:
            channel_num (int): Number of channels in the data.
            original_time_len (int): Original time length of the data.
            patch_len (int): Length of each patch
            permute_freq (int): Number of permutations to apply
            permute_strategy (str, optional): Strategy for permutation. Options: "random", "vicinity", "farthest"
            permute_tensor_size (int, optional): Number of precomputed permutation patterns. Defaults to 1000.
            seed (int, optional): Random seed for reproducibility. Defaults to None.
            load_path (Optional[str], optional): Path to saved shuffled indices data which is generated by `shuffler.PPT` class.
            device (str, optional): Device to run the model on. Defaults to "cpu".
            multiprocessing (bool, optional): Whether to use multiprocessing used to shuffle data. Defaults to False.
            num_workers (int, optional): Number of workers for multiprocessing. Defaults to 8.
        """
        
        if original_time_len % patch_len != 0:
            warnings.warn(f"original_time_len {original_time_len} must be divisible by patch_len {patch_len}. Truncating the data to {original_time_len // patch_len * patch_len}")
            original_time_len = original_time_len // patch_len * patch_len
        
        super().__init__(
            channel_num=channel_num,
            original_time_len=original_time_len,
            patch_len=patch_len,
            permute_freq=permute_freq,
            permute_strategy=permute_strategy,
            permute_tensor_size=permute_tensor_size,
            seed=seed,
            save_path=load_path,
            device=device,
            save_shuffled_index=False,
            multiprocessing=multiprocessing,
            num_workers=num_workers
        )
        
    def forward(self, X: Union[np.ndarray, torch.Tensor], X_shuffled: Optional[Union[np.ndarray, torch.Tensor]] = None, verbose=False) -> np.ndarray:
        """
        Calculate the ACF-COS score for the input
        Given 3D input data (Batch, Channel, Time), it separates the Time dimension into (Patch Length, Patch Number)
        
        Args:
            X (Union[np.ndarray, torch.Tensor]): Input data to calculate ACF-COS score
                if 3D data, shape must be (Batch, Channel, Time)
                if 4D data, shape must be (Batch, Channel, Patch Length, Patch Number)
                
                Given X_shuffled, it must be 3D data (Batch, Channel, Time)
            X_shuffled (Optional[Union[np.ndarray, torch.Tensor]], optional): Shuffled input data to calculate ACF-COS score
                The shape must be (Batch, Channel, Time)
            
        Returns:
            np.ndarray: ACF-COS score for the input data (Channel,)
        """
        
        if X_shuffled is None:
            dim = len(X.shape)
            assert dim in [3, 4], f"Input data must be 3D or 4D, got {dim}D"

            if dim == 3:
                # Truncate the data if the time length is not divisible by patch length
                if X.shape[2] != self.original_time_len:
                    warnings.warn(f"Input time length {X.shape[2]} is not equal to original_time_len {self.original_time_len}. Truncating the data to {self.original_time_len}")
                    X = X[:, :, :self.original_time_len]
                    print(f"Truncated data shape: {X.shape}")
                print("Input ACF-COS data is 3D (Batch, Channel, Time), trying to spilt the Time dimension into (Patch Length, Patch Number)")
                
                X = self._split_time_dim(X)
            
            X = self._to_torch(X)
            X_shuffled = super().forward(X)

            # X_shuffled = (Batch, Channel, Patch Length, Patch Number) -> (Batch, Channel, Time)
            X = rearrange(X, 'b c l p -> b c (p l)')
            X_shuffled = rearrange(X_shuffled, 'b c l p -> b c (p l)')            

        X = self._to_numpy(X)
        X_shuffled = self._to_numpy(X_shuffled)
        
        X_acf = self._acf(X)
        X_shuffled_acf = self._acf(X_shuffled)
        acf_cos = []
        for i in range(X_acf.shape[0]):
            acf_cos.append(
                1 - self._cosine_similarity(X_acf[i], X_shuffled_acf[i])
            )
        acf_cos = np.array(acf_cos)
        
        return acf_cos
        
    def _split_time_dim(self, X: Union[np.ndarray, torch.Tensor]) -> Union[np.ndarray, torch.Tensor]:
        """
        Split the Time dimension of the input data into (Patch Length, Patch Number)
        
        Args:
            X (Union[np.ndarray, torch.Tensor]): Input data to split the Time dimension
                if 3D data, shape must be (Batch, Channel, Time)
        
        Returns:
            Union[np.ndarray, torch.Tensor]: Data with Time dimension split into (Patch Length, Patch Number)
            (Batch, Channel, Patch Length, Patch Number)
        """
        
        assert len(X.shape) == 3, f"Input data must be 3D (Batch, Channel, Time), got {len(X.shape)}D"
        assert X.shape[1] == self.channel_num, f"Input data must have the same number of channels as sample_data, got {X.shape[1]} channels"
        
        _, _, time = X.shape
        valid_time_len = (time // self.patch_len) * self.patch_len
        if valid_time_len != time:
            warnings.warn(f"Input time length {time} is not divisible by patch length {self.patch_len}. Truncating the data to {valid_time_len}")
            X = X[:, :, :valid_time_len]
        
        
        X = rearrange(X, 'b c (p l) -> b c l p', l=self.patch_len, p=self.patch_num)

        return X
    
    def _to_numpy(self, X: Union[np.ndarray, torch.Tensor]) -> np.ndarray:
        """
        Convert input data to numpy array
        
        Args:
            X (Union[np.ndarray, torch.Tensor]): Input data to convert to numpy array
        
        Returns:
            np.ndarray: Numpy array of the input data
        """
        if isinstance(X, torch.Tensor):
            X = X.cpu().detach().numpy()
        return X
    
    def _to_torch(self, X: Union[np.ndarray, torch.Tensor]) -> torch.Tensor:
        """
        Convert input data to torch tensor
        
        Args:
            X (Union[np.ndarray, torch.Tensor]): Input data to convert to torch tensor
            
        Returns:
            torch.Tensor: Torch tensor of the input data
        """
        if isinstance(X, np.ndarray):
            X = torch.tensor(X)
            X = X.to(self.device)
        return X
    
    def _cosine_similarity(self, x: Union[np.ndarray, torch.Tensor], y: Union[np.ndarray, torch.Tensor]) -> Union[np.ndarray, torch.Tensor]:
        """
        Calculate the cosine similarity between two input data
        
        Args:
            x (Union[np.ndarray, torch.Tensor]): First input data
            y (Union[np.ndarray, torch.Tensor]): Second input data
        
        Returns:
            Union[np.ndarray, torch.Tensor]: Cosine similarity between the two input data
        """
        
        x = self._to_numpy(x)
        y = self._to_numpy(y)
        
        dot_product = np.dot(x, y)
        norm_x = np.linalg.norm(x)
        norm_y = np.linalg.norm(y)
        
        return dot_product / (norm_x * norm_y)
    
    def _autocorrelation(self, x: Union[np.ndarray, torch.Tensor]) -> Union[np.ndarray, torch.Tensor]:
        """
        Calculate the autocorrelation function of the input data
        
        Args:
            x (Union[np.ndarray, torch.Tensor]): Input data to calculate the autocorrelation function
        
        Returns:
            Union[np.ndarray, torch.Tensor]: Autocorrelation function of the input data
        """
        
        x = self._to_numpy(x)
        corre = np.correlate(x, x, mode='full')
        return corre[corre.size // 2:]
    
    def _acf(self, data: np.ndarray) -> np.ndarray:
        """
        Calculate the autocorrelation function of the input data
        
        Args:
            data (np.ndarray): Input data to calculate the autocorrelation function
        
        Returns:
            np.ndarray: Autocorrelation function of the input data
        """
        
        flatten_data = data.reshape(-1, data.shape[-1])
        
        acf = []
        for i in range(flatten_data.shape[0]):
            acf.append(self._autocorrelation(flatten_data[i, :]))
        acf = np.array(acf)
        
        return acf
        