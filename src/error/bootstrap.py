import logging
import pickle
import os
import copy
import math
import numpy as np
import pandas as pd
from tqdm import tqdm
import plotly.graph_objects as go
from src.model.nmf import NMF
from pathlib import Path


logger = logging.getLogger("NMF")
logger.setLevel(logging.INFO)


class Bootstrap:
    """
    The Bootstrap (BS) method is used to detect and estimate disproportionate effects of a small set of data samples on
    the solution. The BS method assembles dataset by randomly selecting blocks of consecutive samples from the original
    dataset, with replacement.
    """

    def __init__(self,
                 nmf: NMF,
                 feature_labels: list,
                 model_selected: int = -1,
                 bootstrap_n: int = 20,
                 block_size: int = 10,
                 threshold: float = 0.6,
                 seed: int = None
                 ):
        """
        The BS method implemented here is called the block bootstrap method. The block BS method is useful for use on
        timeseries data that may contain temporal correlations that would otherwise be lost if single samples were
        resampled.

        For each BS run, a unique BS dataset is created and run through NMF to convergence where the output is compared
        to see if the factors of the original base model map to each of the factors of the BS output. The factors are
        mapped to the original base model factors by highest correlation, potentially having multiple BS factors mapping
        to the same base model factor, where the correlation is above the user specified threshold.

        Parameters
        ----------
        nmf : NMF
           A completed NMF base model that used the same data and uncertainty datasets.
        feature_labels : list
           The labels for the features, columns of the dataset, specified from the data handler.
        model_selected : int
           The index of the model selected from a batch NMF run, used for labeling.
        bootstrap_n : int
           The number of bootstrap runs to make.
        block_size : int
           The block size for the BS resampling.
        threshold : float
           The correlation threshold that must be met for a BS factor to be mapped to a base model factor, factor
           correlations must be greater than the threshold or are labeled unmapped.
        seed : int
           The random seed for random resampling of the BS datasets. The base model random seed is used for all BS runs,
           which result in the same initial W matrix.
        """
        self.nmf = nmf
        self.model_selected = model_selected
        self.feature_labels = feature_labels
        self.data = nmf.V
        self.uncertainty = nmf.U

        self.bootstrap_n = bootstrap_n
        self.block_size = block_size
        self.threshold = threshold

        self.base_W = self.nmf.W
        self.base_H = self.nmf.H
        self.base_Q = self.nmf.Qrobust
        self.factors = self.base_H.shape[0]

        self.base_seed = self.nmf.seed
        self.bs_seed = seed if seed is not None else self.base_seed

        self.bs_results = {}
        self.rng = np.random.default_rng(seed=self.bs_seed)
        self.mapping_df = None
        self.q_results = None
        self.factor_tables = {}
        self.bs_profiles = {}
        self.bs_factor_contributions = {}

    def _block_resample(self,
                        data: np.ndarray,
                        uncertainty: np.ndarray,
                        W: np.ndarray,
                        seed: int,
                        overlapping: bool = False
                        ):
        """
        Block resampling will resampled the set of data, uncertainty and W with the same resampling indices. The
        datasets are broken into block_size chunks and then randomly selected with replacement until the new bootstrap
        datasets are the same size as the original datasets. Blocks are constructed from consecutive samples in the
        datasets, i.e. [0, 1, 2, 3], [4, 5, 6, 7], etc...

        A block sized is reduced to fit the existing data, to prevent invalid indexing.

        The for loop assignment of the new indices is necessary due to a bug in the python package that passes the
        modified matrices to the Rust functions.

        Parameters
        ----------
        data : np.ndarray
           The original input dataset, deepcopy (required for Rust functions).
        uncertainty : np.ndarray
           The original uncertainty dataset, deepcopy (required for Rust functions).
         W : np.ndarray
           The base model factor contribution matrix, deepcopy (required for Rust functions).
        seed: int
           The random seed used to randomly resample the datasets.
        overlapping : bool
           Specifies if blocks can overlap, i.e. have blocks [0, 1, 2, 3], [2, 3, 4, 5], etc...

        Returns
        -------
        np.ndarray, np.ndarray, nd.ndarray, list
            The resampled data, uncertainty, and W matrices, and the resampled index matrix for validation.

        """
        N = self.data.shape[0]
        if self.block_size > N/2:
            logging.warn(f"Block size is greater than half the samples of the data. N: {N}. Setting block size to {N/2}")
            self.block_size = N/2
        rng = np.random.default_rng(seed=seed)
        index_blocks = []
        M = math.ceil(N / self.block_size)
        index_count = 0
        if not overlapping:
            for i in range(M):
                block_i = list(range(index_count, index_count + self.block_size))
                while index_count + len(block_i) > N:
                    block_i.pop()
                index_count += self.block_size
                index_blocks.append(block_i)
        index_matrix = []
        row_count = 0
        for i in range(M):
            if not overlapping:
                rng_i = int(rng.integers(low=0, high=M - 1, size=1))
                index_i = index_blocks[rng_i]
            else:
                i_start = int(rng.integers(low=0, high=N - self.block_size - 1, size=1)[0])
                index_i = list(range(i_start, i_start + self.block_size))
            while row_count + len(index_i) > N:
                index_i.pop()
            row_count += len(index_i)
            index_matrix.extend(index_i)
        _data = data[index_matrix]
        _uncertainty = uncertainty[index_matrix]
        _W = W[index_matrix]
        for i in range(_data.shape[0]):
            W[i] = _W[i]
            data[i] = _data[i]
            uncertainty[i] = _uncertainty[i]
        _data = data
        _W = W
        _uncertainty = uncertainty
        return np.array(_data, dtype=np.float64), \
            np.array(_uncertainty, dtype=np.float64), \
            np.array(_W, dtype=np.float64), index_matrix

    def _resample(self,
                  data: np.ndarray,
                  uncertainty: np.ndarray,
                  W: np.ndarray,
                  seed: int
                  ):
        """
        Resamples the datasets with replacement, by single sample.

        Parameters
        ----------
        data : np.ndarray
           The original input dataset, deepcopy (required for Rust functions).
        uncertainty : np.ndarray
           The original uncertainty dataset, deepcopy (required for Rust functions).
         W : np.ndarray
           The base model factor contribution matrix, deepcopy (required for Rust functions).
        seed: int
           The random seed used to randomly resample the datasets.

        Returns
        -------
        np.ndarray, np.ndarray, nd.ndarray, list
            The resampled data, uncertainty, and W matrices, and the resampled index matrix for validation.
        """
        rng = np.random.default_rng(seed=seed)
        random_index = list(rng.choice(range(data.shape[0]), data.shape[0], replace=True))
        _data = data[random_index]
        _uncertainty = uncertainty[random_index]
        _W = W[random_index]
        return np.array(_data, dtype=np.float64), \
            np.array(_uncertainty, dtype=np.float64), \
            np.array(_W, dtype=np.float64), random_index

    def _calculate_factor_correlation(self,
                                      factor1: np.ndarray,
                                      factor2: np.ndarray
                                      ):
        """
        Calculate the correlation between two factors, two 1d arrays.

        Parameters
        ----------
        factor1 : np.ndarray
           The first factor in the comparison.
        factor2 : np.ndarray
           The second factor in the comparison.

        Returns
        -------
        float
           The R squared correlation of the two factors.

        """
        factor1 = factor1.astype(float)
        factor2 = factor2.astype(float)
        corr_matrix = np.corrcoef(factor1, factor2)
        corr = corr_matrix[0, 1]
        r_sq = corr ** 2
        return r_sq

    def map_factors(self,
                    H1: np.ndarray,
                    H2: np.ndarray,
                    threshold: float = 0.6
                    ):
        """
        Map all the factors of one factor profile to the factors of a second factor profile.

        Parameters
        ----------
        H1 : np.ndarray
           The first factor profile for the mapping.
        H2 : np.ndarray
           The second factor profile for the mapping.
        threshold : float
           The threshold that a factor correlation must exceed to be mapped to another factor.

        Returns
        -------
        dict
           A dictionary of the mapping of the H1 factors to the H2 factors.
        """
        mapping = {}
        for i in range(H1.shape[0]):
            f1_i = H1[i]
            best_i = i
            best_r = self._calculate_factor_correlation(f1_i, H2[i])
            for j in range(H2.shape[0]):
                if j == i:
                    pass
                j_r2 = self._calculate_factor_correlation(f1_i, H2[j])
                if j_r2 > best_r:
                    best_r = j_r2
                    best_i = j
            mapping[i] = {"match": best_i, "r2": best_r, "mapped": True if best_r >= threshold else False}
        return mapping

    def map_contributions(self,
                          W1: np.ndarray,
                          H1: np.ndarray,
                          W2: np.ndarray,
                          H2: np.ndarray,
                          threshold: float = 0.6):
        """
        Map all the factors of H1 to the factors of H2 by the factor contributions.

        Parameters
        ----------
        W1 : np.ndarray
           The first factor contribution matrix for the mapping.
        H1 : np.ndarray
           The first factor profile matrix for the mapping.
        W2 : np.ndarray
           The second factor contribution matrix for the mapping.
        H2 : np.ndarray
           The second factor profile matrix for the mapping.
        threshold : float
           The threshold that a factor correlation must exceed to be mapped to another factor.

        Returns
        -------
        dict
           A dictionary of the mapping of the H1 factors to the H2 factors.
        """
        mapping = {}
        matrices1 = {}
        matrices2 = {}
        for i in range(H1.shape[0]):
            H_i = H1[i]
            W_i = W1[:, i]
            W_i = W_i.reshape(len(W_i), 1)
            conc = np.matmul(W_i, [H_i]).sum(axis=0)
            matrices1[i] = conc
            H2_i = H2[i]
            W2_i = W2[:, i]
            W2_i = W2_i.reshape(len(W2_i), 1)
            conc2 = np.matmul(W2_i, [H2_i]).sum(axis=0)
            matrices2[i] = conc2

        for i in range(H1.shape[0]):
            m1_i = matrices1[i]
            best_i = i
            best_r = self._calculate_factor_correlation(m1_i, matrices2[i])
            for j in range(H2.shape[0]):
                if j == i:
                    pass
                j_r2 = self._calculate_factor_correlation(m1_i, matrices2[j])
                if j_r2 > best_r:
                    best_r = j_r2
                    best_i = j
            mapping[i] = {"match": best_i, "r2": best_r, "mapped": True if best_r >= threshold else False}
        return mapping

    def run(self,
            keep_H: bool = True,
            reuse_seed: bool = True,
            block: bool = True,
            overlapping: bool = False
            ):
        """
        Run the BS method.

        Executes all the BS runs and compiles the results.

        Parameters
        ----------
        keep_H : bool
           When retraining the NMF models using the resampled input and uncertainty datasets, keep the base model H
           matrix instead of reinitializing. The W matrix is always reinitialized when NMF is run on the BS datasets.
           Default = True
        reuse_seed : bool
           Reuse the base model seed for initializing the W matrix, and the H matrix if keep_H = False. Default = True
        block : bool
           Use block resampling instead of full resampling. Default = True
        overlapping : bool
           Allow resampled blocks to overlap. Default = False

        """
        self._train(keep_H=keep_H, reuse_seed=reuse_seed, block=block, overlapping=overlapping)
        self._compile_results()

    def _train(self,
               keep_H: bool = True,
               reuse_seed: bool = True,
               block: bool = True,
               overlapping: bool = False
               ):
        """
        Train a new NMF model for each BS dataset.

        Parameters
        ----------
        keep_H : bool
           When retraining the NMF models using the resampled input and uncertainty datasets, keep the base model H
           matrix instead of reinitializing. The W matrix is always reinitialized when NMF is run on the BS datasets.
           Default = True
        reuse_seed : bool
           Reuse the base model seed for initializing the W matrix, and the H matrix if keep_H = False. Default = True
        block : bool
           Use block resampling instead of full resampling. Default = True
        overlapping : bool
           Allow resampled blocks to overlap. Default = False

        """
        #TODO: Implement parallelization
        for i in tqdm(range(1, self.bootstrap_n+1), desc="Bootstrap resampling, training and mapping"):
            sample_seed = self.rng.integers(low=0, high=1e10, size=1)
            _V = copy.deepcopy(self.data)
            _U = copy.deepcopy(self.uncertainty)
            _W = copy.deepcopy(self.base_W)
            _H = copy.deepcopy(self.base_H)
            train_seed = sample_seed
            if block:
                bs_data, bs_uncertainty, bs_W, bs_index = self._block_resample(data=_V,
                                                                               uncertainty=_U,
                                                                               W=_W,
                                                                               seed=sample_seed,
                                                                               overlapping=overlapping)
            else:
                bs_data, bs_uncertainty, bs_W, bs_index = self._resample(data=_V,
                                                                         uncertainty=_U,
                                                                         W=_W,
                                                                         seed=sample_seed)
            if not keep_H:
                _H = None
            if reuse_seed:
                train_seed = self.base_seed
            bs_i_nmf = NMF(V=bs_data, U=bs_uncertainty, factors=self.factors, method=self.nmf.method, seed=train_seed,
                           optimized=self.nmf.optimized, verbose=False)
            bs_i_nmf.initialize(H=_H)
            bs_i_nmf.train(max_iter=self.nmf.metadata["max_iterations"],
                           converge_delta=self.nmf.metadata["converge_delta"],
                           converge_n=self.nmf.metadata["converge_n"])
            bs_i_mapping = self.map_contributions(W1=bs_i_nmf.W, H1=bs_i_nmf.H, W2=self.base_W, H2=self.base_H,
                                                  threshold=self.threshold)
            bs_i_results = {
                "model": bs_i_nmf,
                "index": bs_index,
                "mapping": bs_i_mapping
            }
            self.bs_results[i] = bs_i_results

    def _compile_results(self):
        """
        Generate the statistics and results as shown in PMF5.
        """
        self._build_table()
        self._factor_statistics()
        self._calculate_factors()

    def _calculate_factors(self):
        """
        Calculates the factor distributions from all the BS runs.
        """
        bs_profiles = {}
        for i in range(self.factors):
            profile = []
            for r_k, r_v in self.bs_results.items():
                p_f = r_v["model"].H
                p_fn = p_f / p_f.sum(axis=0)
                profile.append(p_fn[i])
            bs_profiles[i] = profile
        self.bs_profiles = bs_profiles

        bs_factor_contributions = {}
        for i in range(self.factors):
            contributions = []
            for r_k, r_v in self.bs_results.items():
                i_H = [r_v["model"].H[i]]
                i_W = r_v["model"].W[:, i]
                i_W = i_W.reshape(len(i_W), 1)
                i_WH = np.matmul(i_W, i_H)
                i_sum = i_WH.sum(axis=0)
                contributions.append(i_sum)
            bs_factor_contributions[i] = contributions
        self.bs_factor_contributions = bs_factor_contributions

    def _build_table(self):
        """
        Constructs the factor mapping table that is shown in the summary
        """
        qrobust_list = []
        mapping_table = np.zeros(shape=(self.factors, self.factors))
        table_columns = []
        unmapped = np.zeros(shape=(self.factors, 1))
        for i in range(self.factors):
            table_columns.append(f"Base Factor {i+1}")
        for i_k, i_v in self.bs_results.items():
            for j_k, j_v in i_v["mapping"].items():
                if j_v["mapped"]:
                    mapping_table[j_k, j_v["match"]] += 1
                else:
                    unmapped[j_k] += 1
            qrobust_list.append(i_v["model"].Qrobust)
        boots = [f"Boot Factor {i+1}" for i in range(self.factors)]
        mapping_df = pd.DataFrame(mapping_table, columns=table_columns)
        mapping_df["Boot Factors"] = boots
        mapping_df.insert(0, "Boot Factors", mapping_df.pop("Boot Factors"))
        mapping_df["Unmapped"] = unmapped
        self.mapping_df = mapping_df
        self.q_results = pd.DataFrame(qrobust_list, columns=["Q(robust)"])

    def _factor_statistics(self):
        """
        Assemble the factor statistics as shown in PMF5.
        """
        factor_tables = {}
        for i in range(self.factors):
            factor_results = []
            for j_k, j_v in self.bs_results.items():
                factor_results.append(j_v["model"].H[i])
            factor_tables[i] = factor_results
        self.factor_tables = factor_tables

    def summary(self):
        """
        Prints a summary of the BS parameters and results. Recreates the output provided in the PMF5 BS Summary.
        """
        print("NMF Bootstrap Error Estimation Summary")
        print("----- Input Parameters -----")
        print(f"Base model run number: {self.model_selected}")
        print(f"Number of bootstrap runs: {self.bootstrap_n}")
        print(f"Min. Correlation R-Value: {self.threshold}")
        print(F"Number of Factors: {self.factors}")
        print("\n")
        self.show_mapping_table()
        self.show_q_table()
        for i in range(1, self.factors+1):
            self.show_factor_results(factor=i)

    def show_mapping_table(self):
        """
        Plots the factor mapping table.
        """
        mapping_table = go.Figure(data=[go.Table(header=dict(values=self.mapping_df.columns),
                                                 cells=dict(values=self.mapping_df.values.T)
        )])
        mapping_table.update_layout(title="Mapping of bootstrap factors to base factors",
                                    width=1200, height=300, margin={'t': 50, 'l': 25, 'b': 10, 'r': 25})
        mapping_table.show()

    def show_q_table(self):
        """
        Plots the BS run Q(robust) statistics.
        """
        q_table = go.Figure(data=[go.Table(header=dict(values=["Base", "Min", "25th", "Median", "75th", "Max"]),
            cells=dict(values=[round(self.base_Q), round(self.q_results.min()), round(self.q_results.quantile(0.25)),
                               round(self.q_results.median()), round(self.q_results.quantile(0.75)),
                               round(self.q_results.max())])
        )])
        q_table.update_layout(title="Q(Robust) Percentile Report",
                              width=1200, height=200, margin={'t': 50, 'l': 25, 'b': 10, 'r': 25})
        q_table.show()

    def show_factor_results(self, factor: int):
        """
        Create the table showing the factor metrics from the BS runs for a specific factor.

        Parameters
        ----------
        factor : int
           The index of the factor to show.

        """
        if factor > self.factors or factor < 1:
            print(f"Invalid factor provided, must be between 1 and {self.factors}")
            return
        factor_label = factor
        factor = factor - 1
        factor_results = self.factor_tables[factor]
        factor_df = pd.DataFrame(factor_results, columns=self.feature_labels)
        factor_df[factor_df < 1e-5] = 0.0
        base_factor = self.base_H[factor]
        base_factor[base_factor < 1e-5] = 0.0
        base_df = pd.DataFrame(base_factor.reshape(1, len(base_factor)), columns=self.feature_labels)
        round_value = 6
        q3 = factor_df.quantile(0.75)
        q1 = factor_df.quantile(0.25)
        iqr = base_df.iloc[0].between(q1, q3)
        results = {"features": self.feature_labels, "Base Run Profile": base_factor.round(round_value), "Within IQR": iqr.values,
                   "BS Mean": factor_df.mean().values.round(round_value), "BS Std. Dev.": factor_df.std().values.round(round_value),
                   "BS 5th": factor_df.quantile(0.05).values.round(round_value),
                   "BS 25th": factor_df.quantile(0.25).values.round(round_value), "BS Median": factor_df.median().values.round(round_value),
                   "BS 75th": factor_df.quantile(0.75).values.round(round_value), "BS 95th": factor_df.quantile(0.95).values.round(round_value)}
        factor_summary_table = go.Figure(data=[go.Table(
            header=dict(values=list(results.keys())),
            cells=dict(values=list(results.values()))
        )])
        factor_summary_table.update_layout(title=f"Bootstrap run uncertainty statistics - Factor {factor_label}",
                                           width=1800, height=1000, margin={'t': 50, 'l': 25, 'b': 10, 'r': 25})
        factor_summary_table.show()

    def plot_factor(self, factor: int):
        """
        Plot the BS factor profile for a specific factor.

        Parameters
        ----------
        factor : int
           The index of the factor to plot.

        """
        if factor > self.factors or factor < 1:
            print(f"Invalid factor provided, must be between 1 and {self.factors}")
            return
        factor_label = factor
        factor = factor - 1

        base_data = self.base_H
        base_ndata = base_data / base_data.sum(axis=0)
        f_data = np.array(self.bs_profiles[factor])

        f_plot = go.Figure()
        for i in range(len(self.feature_labels)):
            i_data = 100 * f_data[:, i]
            f_plot.add_trace(go.Box(name=self.feature_labels[i], y=i_data, boxpoints='outliers', notched=True,
                                    marker_color='rgb(107,174,214)', line_color='rgb(107,174,214)', marker_size=4,
                                    line_width=1))
        f_plot.add_trace(go.Scatter(x=self.feature_labels, y=100 * base_ndata[factor], mode='markers',
                                    marker=dict(color='red', size=4), name="Base"))
        f_plot.update_layout(
            title=f"Variability in Percentage of Species - Model {self.model_selected} - Factor {factor_label} ",
            width=1200, height=600, showlegend=False)
        f_plot.update_yaxes(title_text="Percentage", range=[0, 100])
        f_plot.show()

    def plot_contribution(self, factor: int):
        """
        Plot the BS factor contributions for a specific factor.

        Parameters
        ----------
        factor : int
           The index of the factor to plot.

        """
        if factor > self.factors or factor < 1:
            print(f"Invalid factor provided, must be between 1 and {self.factors}")
            return
        factor_label = factor
        factor = factor - 1

        base_Wi = self.base_W[:, factor]
        base_Wi = base_Wi.reshape(len(base_Wi), 1)
        base_Hi = [self.base_H[factor]]
        base_sums = np.matmul(base_Wi, base_Hi).sum(axis=0)
        base_sums[base_sums < 1e-4] = 1e-4
        c_data = np.array(self.bs_factor_contributions[factor])
        c_plot = go.Figure()
        for i in range(len(self.feature_labels)):
            i_data = c_data[:, i]
            i_data[i_data < 1e-4] = 1e-4
            c_plot.add_trace(go.Box(name=self.feature_labels[i], y=i_data, boxpoints='outliers', notched=True,
                                    marker_color='rgb(107,174,214)', line_color='rgb(107,174,214)', marker_size=4,
                                    line_width=1))
        c_plot.add_trace(
            go.Scatter(x=self.feature_labels, y=base_sums, mode='markers', marker=dict(color='red', size=4),
                       name="Base"))
        c_plot.update_layout(
            title=f"Variability in Concentration of Species - Model {self.model_selected} - Factor {factor_label} ",
            width=1200, height=600, showlegend=False)
        c_plot.update_yaxes(title_text="Concentration (log)", type="log")
        c_plot.show()

    def plot_results(self, factor: int):
        """
        Plot both the factor profile and factor contributions for a specific index.

        Parameters
        ----------
        factor : int
           The index of the factor to plot.

        """
        self.plot_factor(factor=factor)
        self.plot_contribution(factor=factor)

    def save(self, bs_name: str,
             output_directory: str
             ):
        """
        Save the BS results.
        Parameters
        ----------
        bs_name : str
            The name to use for the BS file.
        output_directory :
            The output directory to save the BS file to.

        Returns
        -------
        str
           The path to the saved file.

        """
        output_directory = Path(output_directory)
        if not output_directory.is_absolute():
            current_directory = os.path.abspath(__file__)
            output_directory = Path(os.path.join(current_directory, output_directory)).resolve()
        if os.path.exists(output_directory):
            file_path = os.path.join(output_directory, f"{bs_name}.pkl")
            with open(file_path, "wb") as save_file:
                pickle.dump(self, save_file)
                logger.info(f"BS NMF output saved to pickle file: {file_path}")
            return file_path
        else:
            logger.error(f"Output directory does not exist. Specified directory: {output_directory}")
            return None

    @staticmethod
    def load(file_path: str):
        """
        Load a previously saved BS NMF pickle file.

        Parameters
        ----------
        file_path : str
           File path to a previously saved BS NMF pickle file

        Returns
        -------
        Bootstrap
           On successful load, will return a previously saved BS NMF object. Will return None on load fail.
        """
        file_path = Path(file_path)
        if not file_path.is_absolute():
            current_directory = os.path.abspath(__file__)
            file_path = Path(os.path.join(current_directory, file_path)).resolve()
        if os.path.exists(file_path):
            try:
                with open(file_path, "rb") as pfile:
                    bs = pickle.load(pfile)
                    return bs
            except pickle.PickleError as p_error:
                logger.error(f"Failed to load Bootstrap pickle file {file_path}. \nError: {p_error}")
                return None
        else:
            logger.error(f"Bootstrap load file failed, specified pickle file does not exist. File Path: {file_path}")
            return None
