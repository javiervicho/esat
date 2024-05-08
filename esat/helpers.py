import logging
import os

import numpy as np
import pandas as pd
import multiprocessing as mp
import plotly.graph_objects as go
from esat.model.sa import SA
from tqdm import tqdm

logger = logging.getLogger(__name__)


class FSearch:
    def __init__(self, V, U, seed: int = 42, test_percent: float = 0.1):
        self.V = V
        self.U = U
        self.seed = seed
        self.rng = np.random.default_rng(seed=self.seed)
        self.test_percent = test_percent

        self.min_factors = 2
        self.max_factors = 15
        self.samples = 200
        self.pbar = None
        self.train_mse = None
        self.test_mse = None
        self.estimated_factor = None
        self.results_df = None

    def _get_mask(self, threshold=0.1):
        _mask = np.zeros(shape=self.V.shape)
        for feature in range(self.V.shape[1]):
            feature_i = self.rng.random(size=self.V[feature].shape) > threshold
            _mask[feature] = feature_i
        return _mask.astype(int)

    def _update_pbar(self, results):
        list_i = results[2]-self.min_factors
        self.train_mse[list_i].append(results[0])
        self.test_mse[list_i].append(results[1])
        self.pbar.update(1)

    @staticmethod
    def _random_sample(V, U, mask, seed, factor_n):
        m_train = np.count_nonzero(mask)
        m_test = np.count_nonzero(~mask)
        _sa = SA(V=V, U=U, factors=factor_n, method="ls-nmf", seed=seed, optimized=True, verbose=False)
        _sa.initialize()
        _sa.train(max_iter=10000, converge_delta=1.0, converge_n=10)
        residuals = V - _sa.WH
        train_residuals = np.multiply(mask, residuals**2)
        test_residuals = np.multiply(~mask, residuals**2)
        train_mse = np.round(train_residuals.sum()/m_train, 5)
        test_mse = np.round(test_residuals.sum()/m_test, 5)
        return train_mse, test_mse, factor_n

    def search(self, samples: int = 200, min_factors: int = 2, max_factors: int = 15):
        self.min_factors = min_factors
        self.max_factors = max_factors + 1
        self.samples = samples
        self.pbar = tqdm(total=samples, desc="Rapid random sampling for factor estimation")

        self.train_mse = [[] for i in range(self.max_factors - self.min_factors)]
        self.test_mse = [[] for i in range(self.max_factors - self.min_factors)]

        pool_parameters = []
        for i in range(samples):
            seed_i = self.rng.integers(low=100, high=1e6, size=1)[0]
            factor_i = self.rng.integers(low=self.min_factors, high=self.max_factors, size=1)[0]
            mask = self._get_mask(threshold=self.test_percent)
            pool_parameters.append((self.V, self.U, mask, seed_i, factor_i))

        pool = mp.Pool(os.cpu_count()-1)
        results = []
        for p_parameter in pool_parameters:
            r = pool.apply_async(self._random_sample, p_parameter, callback=self._update_pbar)
            results.append(r)
        for r in results:
            r.wait()
        for r in results:
            r.get()
        pool.close()
        pool.join()
        self.pbar.close()

        self.train_mse = [np.mean(i) for i in self.train_mse]
        self.test_mse = [np.mean(i) for i in self.test_mse]
        return self.results()

    def results(self):
        delta_mse_r = []
        for factor_n in range(0, len(self.test_mse) - 1):
            delta_i = self.test_mse[factor_n] - self.test_mse[factor_n + 1]
            delta_mse_r.append(delta_i)
        c = np.max(delta_mse_r) * 0.01
        ratio_delta = [np.nan]
        for factor_n in range(0, len(self.test_mse) - 2):
            rd = delta_mse_r[factor_n] / (delta_mse_r[factor_n + 1] + c)
            ratio_delta.append(rd)
        ratio_delta.append(np.nan)
        delta_mse = [np.nan]
        for factor_n in range(0, len(self.test_mse) - 1):
            delta_i = self.test_mse[factor_n] - self.test_mse[factor_n + 1]
            delta_mse.append(delta_i)
        self.estimated_factor = np.nanargmax(ratio_delta) + self.min_factors
        logger.info(f"Estimated factor count: {self.estimated_factor}")
        self.results_df = pd.DataFrame(data=
                                       {
                                           "Factors": list(range(self.min_factors, self.max_factors)),
                                           "Test MSE": self.test_mse,
                                           "Train MSE": self.train_mse,
                                           "Delta MSE": delta_mse,
                                           "Delta Ratio": ratio_delta
                                       })
        return self.results_df

    def plot(self, actual_count: int = None):
        mse_fig = go.Figure()
        x = list(range(self.min_factors, self.max_factors))
        mse_fig.add_trace(go.Scatter(x=x, y=self.results_df["Train MSE"], name="Train MSE", mode='lines+markers'))
        mse_fig.add_trace(go.Scatter(x=x, y=self.results_df["Test MSE"], name="Test MSE", mode='lines+markers'))
        mse_fig.add_trace(go.Scatter(x=x, y=self.results_df["Delta MSE"], name="Delta MSE", mode='lines+markers'))
        mse_fig.add_trace(go.Scatter(x=x, y=self.results_df["Delta Ratio"], name="Ratio Delta", mode='lines+markers'))
        if actual_count:
            mse_fig.add_vline(x=actual_count, line_width=1, line_dash="dash", line_color="black",
                              name="Actual Factor Count")
        mse_fig.add_vline(x=self.estimated_factor, line_width=1, line_dash="dash", line_color="red",
                          name="Estimated Factor Count")
        mse_fig.update_layout(width=800, height=800, title_text="Factor Estimation", hovermode='x')
        mse_fig.update_yaxes(title_text="Mean Squared Error")
        mse_fig.update_xaxes(title_text="Number of Factors")
        mse_fig.show()
