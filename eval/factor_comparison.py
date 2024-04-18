import math
import os
import json
import numpy as np
import pandas as pd
from itertools import permutations, combinations
import multiprocessing as mp
from tqdm import tqdm
from esat.model.batch_sa import BatchSA
from esat.metrics import q_loss


class FactorCompare:

    def __init__(self,
                 input_df,
                 uncertainty_df,
                 base_profile_df,
                 base_contribution_df,
                 factors_columns,
                 features,
                 batch_sa,
                 sa_output_file=None,
                 method="all",
                 selected_model=None,
                 ):

        self.input_df = input_df
        self.uncertainty_df = uncertainty_df
        self.base_profile_df = base_profile_df
        self.base_contribution_df = base_contribution_df
        self.base_WH = {}
        self.base_V_estimate = None

        self.factors = len(factors_columns)
        self.features = features

        self.factor_columns = factors_columns

        self._calculate_base_wh()

        self.sa_output_file = sa_output_file
        self.batch_sa = batch_sa
        self.sa_model_dfs = {}
        self.sa_Q = {}
        self._parse_sa_output()

        self.selected_model = selected_model

        self.factor_map = None
        self.best_model = None
        self.best_factor_r = None
        self.best_avg_r = None
        self.best_factor_r_avg = None
        self.best_contribution_r = None
        self.best_contribution_r_avg = None
        self.best_wh_r = None
        self.best_wh_r_avg = None

        self.method = method if method in ('all', 'H', 'W', 'WH') else 'all'
        # 'all': equal weight between correlation of W, H and WH, 'H': only, 'W': only, 'WH': only

    @staticmethod
    def load_pmf_output(factors: int,
                        input_df: pd.DataFrame,
                        uncertainty_df: pd.DataFrame,
                        pmf_profile_file: str,
                        pmf_contribution_file: str,
                        batch_sa: BatchSA):
        if not os.path.exists(pmf_profile_file):
            print(f"No pmf profile file found at: {pmf_profile_file}")
            return
        if not os.path.exists(pmf_contribution_file):
            print(f"No pmf contribution file found at: {pmf_contribution_file}")
            return

        profiles = factors + 2

        pmf_profiles = []
        pmf_profile_p = []
        pmf_profile_t = []

        column_labels = None
        features = input_df.columns

        with open(pmf_profile_file, 'r') as open_file:
            profile_strings = open_file.read()
            t = profile_strings.split('\n')
            j = 0
            for line in t:
                i = line.split('\t')
                if len(i) == profiles:
                    if i[0] == '' and i[1] == '':
                        i[0] = "run"
                        i[1] = "species"
                        column_labels = i
                        continue
                    if j < len(features):
                        pmf_profiles.append(i)
                    elif j < 2 * len(features):
                        pmf_profile_p.append(i)
                    elif j < 3 * len(features):
                        pmf_profile_t.append(i)
                    j += 1
            pmf_profiles_df = pd.DataFrame(pmf_profiles, columns=column_labels)
            # pmf_profile_p_df = pd.DataFrame(pmf_profile_p, columns=column_labels)
            # pmf_profile_t_df = pd.DataFrame(pmf_profile_t, columns=column_labels)
            pmf_profiles_df.drop('run', axis=1, inplace=True)
            # pmf_profile_p_df.drop('run', axis=1, inplace=True)
            # pmf_profile_t_df.drop('run', axis=1, inplace=True)

        df_columns = list(pmf_profiles_df.columns)

        factor_columns = df_columns[1:]
        factor_types = {}
        for f in factor_columns:
            factor_types[f] = 'float'
        pmf_profiles_df = pmf_profiles_df.astype(factor_types)
        # pmf_profile_p_df = pmf_profile_p_df.astype(factor_types)
        # pmf_profile_t_df = pmf_profile_t_df.astype(factor_types)

        column_row = 4
        data_start_row = 5
        dates = []
        pmf_contribution_data = []
        pmf_contribution_columns = None

        with open(pmf_contribution_file, 'r') as open_file:
            contribution_strings = open_file.read()
            rows = contribution_strings.split('\n')
            for i, row in enumerate(rows):
                if i == column_row - 1:
                    pmf_contribution_columns = row.split('\t')[2:]
                elif i >= data_start_row - 1:
                    row_cells = row.split('\t')
                    if len(row_cells) > 1:
                        dates.append(row_cells[1])
                        pmf_contribution_data.append(row_cells[2:])
        pmf_contribution_df = pd.DataFrame(pmf_contribution_data, columns=pmf_contribution_columns)
        pmf_contribution_df["Datetime"] = dates

        factor_types = {}
        for f in pmf_contribution_columns:
            factor_types[f] = 'float'
        pmf_contribution_df = pmf_contribution_df.astype(factor_types)
        fc = FactorCompare(input_df=input_df,
                        uncertainty_df=uncertainty_df,
                        base_profile_df=pmf_profiles_df,
                        base_contribution_df=pmf_contribution_df,
                        factors_columns=factor_columns,
                        features=features,
                        batch_sa=batch_sa
                        )
        return fc

    def _calculate_base_wh(self):
        if self.base_profile_df is not None and self.base_contribution_df is not None:
            for factor in self.factor_columns:
                base_W_f = self.base_contribution_df[factor].to_numpy()
                base_H_f = self.base_profile_df[factor].to_numpy()
                base_W_f = base_W_f.reshape(len(base_W_f), 1)
                base_WH_f = np.multiply(base_W_f, base_H_f)
                self.base_WH[factor] = base_WH_f
            base_W = self.base_contribution_df[self.factor_columns].to_numpy()
            base_H = self.base_profile_df[self.factor_columns].to_numpy()
            self.base_V_estimate = np.matmul(base_W, base_H.T)

    def _parse_sa_output(self):
        if self.batch_sa is None:
            if not os.path.exists(self.sa_output_file):
                print(f"No sa output found at: {self.sa_output_file}")
                return
            else:
                self.batch_sa = BatchSA.load(self.sa_output_file)
        species_columns = self.features
        for i, i_sa in enumerate(self.batch_sa.results):
            if i_sa is None:
                continue
            sa_h_data = i_sa.H
            sa_w_data = i_sa.W
            sa_wh_data = i_sa.WH
            sa_wh_data = sa_wh_data.reshape(sa_wh_data.shape[1], sa_wh_data.shape[0])

            sa_h_df = pd.DataFrame(sa_h_data, columns=species_columns, index=self.factor_columns)
            sa_w_df = pd.DataFrame(sa_w_data, columns=self.factor_columns)
            sa_wh_df = pd.DataFrame(sa_wh_data.T, columns=species_columns)

            sa_wh_e = {}
            for factor in self.factor_columns:
                sa_H_f = sa_h_df.loc[factor].to_numpy()
                sa_W_f = sa_w_df[factor].to_numpy()
                sa_W_f = sa_W_f.reshape(len(sa_W_f), 1)
                sa_WH_f = np.multiply(sa_W_f, sa_H_f)
                sa_wh_e[factor] = sa_WH_f

            self.sa_model_dfs[i] = {"WH": sa_wh_df, "W": sa_w_df, "H": sa_h_df, 'WH-element': sa_wh_e}
            self.sa_Q[i] = i_sa.Qtrue

    def compare(self, verbose: bool = True):
        base_Q = q_loss(V=self.input_df.to_numpy(),
                        U=self.input_df.to_numpy(),
                        W=self.base_contribution_df[self.factor_columns].to_numpy(),
                        H=self.base_profile_df[self.factor_columns].to_numpy().T
                        )
        correlation_results = {}
        contribution_results = {}
        wh_results = {}
        for m in tqdm(range(len(self.sa_model_dfs)), desc="Calculating correlation between factors from each model"):
            if self.selected_model is not None:
                if m not in self.selected_model:
                    continue
            correlation_results[m] = {}
            contribution_results[m] = {}
            wh_results[m] = {}
            nmf_m = self.sa_model_dfs[m]["H"]
            nmf_contribution_m = self.sa_model_dfs[m]["W"]
            nmf_wh = self.sa_model_dfs[m]["WH-element"]
            for i in self.factor_columns:
                base_i = self.base_profile_df[i].astype(float)
                base_contribution_i = self.base_contribution_df[i].astype(float)
                base_wh = self.base_WH[i].flatten()
                for j in self.factor_columns:
                    nmf_j = nmf_m.loc[j].astype(float)
                    r2 = self.calculate_correlation(factor1=base_i, factor2=nmf_j)
                    correlation_results[m][f"base-{i}_esat-{j}"] = r2
                    nmf_contribution_j = nmf_contribution_m[j].astype(float)
                    r2_2 = self.calculate_correlation(factor1=base_contribution_i, factor2=nmf_contribution_j)
                    contribution_results[m][f"base-{i}_esat-{j}"] = r2_2
                    nmf_wh_f = nmf_wh[j].astype(float).flatten()
                    r2_3 = self.calculate_correlation(base_wh, nmf_wh_f)
                    wh_results[m][f"base-{i}_esat-{j}"] = r2_3

        # factor_permutations = list(permutations(self.factor_columns, len(self.factor_columns)))
        print(f"Number of permutations for {self.factors} factors: {math.factorial(self.factors)}")
        best_r = 0.0
        best_perm = None
        best_model = None
        best_factor_r = None
        best_contribution_r = None
        best_contribution_r_avg = None
        best_factor_r_avg = None
        best_wh_r = None
        best_wh_r_avg = None

        permutations_n = math.factorial(self.factors)
        factors_max = 100000

        pool = mp.Pool()

        for m in tqdm(range(len(self.sa_model_dfs)), desc="Calculating average correlation for all permutations for each model"):
            # Each Model
            if self.selected_model is not None:
                if m not in self.selected_model:
                    continue
            permutation_results = {}
            model_contribution_results = {}
            factor_contribution_results = {}

            factor_permutations = []
            for factor_i, factor in enumerate(list(permutations(self.factor_columns, len(self.factor_columns)))):
                factor_permutations.append(factor)
                if len(factor_permutations) >= factors_max or factor_i == permutations_n - 1:
                    pool_inputs = [(factor, correlation_results[m], contribution_results[m], wh_results[m]) for factor in factor_permutations]
                    for pool_results in pool.starmap(self.combine_factors, pool_inputs):
                        factor, r_avg, r_values, c_r_avg, c_r_values, wh_r_avg, wh_r_values = pool_results
                        permutation_results[factor] = (r_avg, r_values)
                        model_contribution_results[factor] = (c_r_avg, c_r_values)
                        factor_contribution_results[factor] = (wh_r_avg, wh_r_values)

                        if self.method == "all":
                            model_avg_r = (r_avg + c_r_avg + wh_r_avg) / 3.0
                        elif self.method == "W":
                            model_avg_r = c_r_avg
                        elif self.method == "H":
                            model_avg_r = r_avg
                        elif self.method == "WH":
                            model_avg_r = wh_r_avg

                        if model_avg_r > best_r:
                            best_r = model_avg_r
                            best_perm = factor
                            best_model = m
                            best_factor_r = r_values
                            best_factor_r_avg = r_avg
                            best_contribution_r = c_r_values
                            best_contribution_r_avg = c_r_avg
                            best_wh_r = wh_r_values
                            best_wh_r_avg = wh_r_avg
                    factor_permutations = []
        self.best_model = best_model
        self.best_factor_r = best_factor_r
        self.best_avg_r = best_r
        self.best_factor_r_avg = best_factor_r_avg
        self.best_contribution_r = best_contribution_r
        self.best_contribution_r_avg = best_contribution_r_avg
        self.best_wh_r = best_wh_r
        self.best_wh_r_avg = best_wh_r_avg
        self.factor_map = list(best_perm)
        if verbose:
            print(f"R2 - Model: {best_model+1}, Best permutations: {list(best_perm)}, Average R2: {self.best_avg_r}, \n"
                  f"Profile R2 Avg: {self.best_factor_r_avg}, Contribution R2 Avg: {self.best_contribution_r_avg}, "
                  f"WH R2 Avg: {self.best_wh_r_avg}\n"
                  f"Profile R2: {self.best_factor_r}, \n"
                  f"Contribution R2: {self.best_contribution_r}, \n"
                  f"WH R2: {self.best_wh_r}\n"
                  )
            print(f"Base Q(true): {base_Q}, SA Model {best_model+1} Q(true): {self.sa_Q[best_model]}")

    @staticmethod
    def calculate_correlation(factor1, factor2):
        f1 = factor1.astype(float)
        f2 = factor2.astype(float)
        corr_matrix = np.corrcoef(f2, f1)
        corr = corr_matrix[0, 1]
        r_sq = corr ** 2
        return r_sq

    def combine_factors(self, factors, model_correlation, model_contributions, factor_contributions):
        r_values = []
        r_values_2 = []
        r_values_3 = []
        for i, f in enumerate(factors):
            r2 = model_correlation[f"base-{self.factor_columns[i]}_esat-{f}"]
            r2_2 = model_contributions[f"base-{self.factor_columns[i]}_esat-{f}"]
            r2_3 = factor_contributions[f"base-{self.factor_columns[i]}_esat-{f}"]
            r_values.append(r2)
            r_values_2.append(r2_2)
            r_values_3.append(r2_3)
        r_avg = np.mean(r_values)
        r_avg_2 = np.mean(r_values_2)
        r_avg_3 = np.mean(r_values_3)
        return factors, r_avg, r_values, r_avg_2, r_values_2, r_avg_3, r_values_3

