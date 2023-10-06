import os
import sys
import logging
import copy
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.figure_factory as ff
from datetime import datetime, timedelta

logging.basicConfig(format='%(asctime)s - %(message)s', datefmt='%d-%b-%y %H:%M:%S', level=logging.DEBUG)
logger = logging.getLogger()

EPSILON = sys.float_info.min
ROOT_DIR = os.path.join(os.path.abspath(__file__), "..", "..", "..")


class DataHandler:
    """

    """
    def __init__(self, input_path: str, uncertainty_path: str, features: list = None,
                 index_col: str = None, drop_col: list = None, generate_data: bool = False, sn_threshold: float = 2.0):
        """
        Check, load and prep the input and output data paths/files.
        :param input_path: The path to the concentration data file
        :param uncertainty_path: The path to the uncertainty data file
        """
        self.generate_data = generate_data

        self.input_path = input_path
        self.uncertainty_path = uncertainty_path
        self.error = False
        self.error_list = []

        self.input_data = None
        self.uncertainty_data = None

        self.sn_mask = None
        self.sn_threshold = sn_threshold

        self.metrics = None

        self.input_data_processed = None
        self.uncertainty_data_processed = None

        self.index_col = index_col
        self.drop_col = drop_col

        self.features = features
        self.min_values = None
        self.max_values = None

        self.features = None
        self.metadata = {}

        if not self.generate_data:
            self._check_paths()
            self._load_data()

    def _check_paths(self):
        """
        Check all data paths for errors
        :return: None
        """
        if not os.path.isabs(self.input_path):
            self.input_path = os.path.join(ROOT_DIR, self.input_path)
        if not os.path.isabs(self.uncertainty_path):
            self.uncertainty_path = os.path.join(ROOT_DIR, self.uncertainty_path)

        if not os.path.exists(self.input_path):
            self.error = True
            self.error_list.append(f"Input file not found at {self.input_path}")
        if not os.path.exists(self.uncertainty_path):
            self.error = True
            self.error_list.append(f"Uncertainty file not found at {self.uncertainty_path}")
        if self.error:
            logger.error("File Errors: " + ", ".join(self.error_list))
            sys.exit()
        else:
            logger.info("Input and output configured successfully")

    def _set_dataset(self, data, uncertainty):
        if isinstance(data, pd.DataFrame) and isinstance(uncertainty, pd.DataFrame):
            sn = data/uncertainty
            data_mask = data.mask(sn < self.sn_threshold, 0.5)
            data_mask = data_mask.mask(sn >= self.sn_threshold, 1.0)
            self.sn_mask = data_mask.to_numpy()

        if isinstance(data, pd.DataFrame) or isinstance(data, pd.Series):
            data = data.to_numpy()
        if isinstance(uncertainty, pd.DataFrame) or isinstance(uncertainty, pd.Series):
            uncertainty = uncertainty.to_numpy()

        data[data < 0] = EPSILON
        uncertainty[uncertainty < 0] = EPSILON

        self.input_data_processed = data.astype("float32")
        self.uncertainty_data_processed = uncertainty.astype("float32")

    def __read_data(self, filepath, index_col=None):
        if ".csv" in filepath:
            if index_col:
                data = pd.read_csv(filepath, index_col=index_col)
            else:
                data = pd.read_csv(filepath)
        elif ".txt" in filepath:
            if index_col:
                data = pd.read_table(filepath, index_col=index_col, sep="\t")
            else:
                data = pd.read_table(filepath, sep="\t")
            data.dropna(inplace=True)
        else:
            logger.warn("Unknown file type provided.")
            sys.exit()
        return data

    def create_data(self, sample_count: int, species_count: int, value_min: float = 0.1, value_max: float = 10.0,
                      uncertainty_min_p: float = 0.02, uncertainty_max_p: float = 0.06, seed: int = 42):
        """
        Generate random data for testing.
        :param sample_count: Total number of samples to generate (timesteps) (N)
        :param species_count: Total number of species/features to generate (M)
        :param value_min: The minimum value of a sample
        :param value_max: The maximum value of a sample
        :param uncertainty_min_p: The minimum percentage of a sample value to assign uncertainty
        :param uncertainty_max_p: The maximum percentage of a sample value to assign uncertainty
        :param seed: The random generator seed.
        :return:
        """
        rng = np.random.default_rng(seed)

        data = rng.uniform(low=value_min, high=value_max, size=(sample_count, species_count))

        labels = [f"species_{i+1}" for i in range(0, species_count)]

        i_date = datetime.now()
        dates = [(i_date - timedelta(hours=i)).strftime("%m/%d/%Y %H") for i in range(sample_count, 0, -1)]

        uncertainty_p = rng.uniform(low=uncertainty_min_p, high=uncertainty_max_p, size=(sample_count, species_count))
        uncertainty = np.multiply(data, uncertainty_p)

        data_df = pd.DataFrame(data=data, columns=labels)
        data_df["Date"] = dates
        data_df.set_index("Date", inplace=True)

        uncertainty_df = pd.DataFrame(data=uncertainty, columns=labels)
        uncertainty_df["Date"] = dates
        uncertainty_df.set_index("Date", inplace=True)

        self.features = labels
        self.input_data = data_df
        self.uncertainty_data = uncertainty_df
        self.input_data.to_csv(self.input_path)
        self.uncertainty_data.to_csv(self.uncertainty_path)

        self._load_data(existing_data=True)

    def _load_data(self, existing_data: bool = False):
        """
        Loads the input and uncertainty data
        :return: None
        """
        if self.error:
            logger.warn("Unable to load data because of setup errors.")
            return
        if not existing_data:
            self.input_data = self.__read_data(filepath=self.input_path, index_col=self.index_col)
            self.uncertainty_data = self.__read_data(filepath=self.uncertainty_path, index_col=self.index_col)
            self.features = list(self.input_data.columns) if self.features is None else self.features

        if self.drop_col is not None:
            _input_data = self.input_data.drop(self.drop_col, axis=1)
            _uncertainty_data = self.uncertainty_data.drop(self.drop_col, axis=1)
        else:
            _input_data = self.input_data
            _uncertainty_data = self.uncertainty_data

        for f in self.features:
            _input_data[f] = pd.to_numeric(_input_data[f])
            _uncertainty_data[f] = pd.to_numeric(_uncertainty_data[f])

        input_nans = _input_data.isna()
        self._set_dataset(_input_data, _uncertainty_data)

        # self.min_values = self.input_data.min(axis=0).combine(self.uncertainty_data.min(axis=0), min)
        # self.max_values = self.input_data.max(axis=0).combine(self.uncertainty_data.max(axis=0), max)
        self.min_values = _input_data.min(axis=0)
        self.max_values = _input_data.max(axis=0)

        c_df = _input_data.copy()
        u_df = _uncertainty_data.copy()

        min_con = c_df.min()
        p25 = c_df.quantile(q=0.25, numeric_only=True)
        median_con = c_df.median(numeric_only=True)
        p75 = c_df.quantile(q=0.75, numeric_only=True)
        max_con = c_df.max()

        d = (c_df - u_df).divide(u_df, axis=0)
        mask = c_df <= u_df
        d.mask(mask, 0, inplace=True)
        sn = (1 / d.shape[0]) * d.sum(axis=0)

        categories = ["Strong"] * d.shape[1]

        self.metrics = pd.DataFrame(
            data={"Category": categories, "S/N": sn, "Min": min_con, "25th": p25, "50th": median_con, "75th": p75,
                  "Max": max_con})

    def remove_outliers(self, quantile: float = 0.8, drop_min: bool = True, drop_max: bool = False):
        """
        Remove outliers from the input dataset
        :param quantile:
        :param drop_min:
        :param drop_max:
        :return:
        """
        if self.error:
            logger.warn("Unable to process input data because of setup errors")
            return
        temp_data = copy.copy(self.input_data)
        temp_uncertainty = copy.copy(self.uncertainty_data)
        max_q_value = temp_data.quantile(quantile, axis=0)
        min_q_value = temp_data.quantile(1.0-quantile, axis=0)

        if drop_max:
            max_outlier_mask = temp_data.ge(max_q_value)
            temp_data = temp_data.mask(max_outlier_mask, np.nan)
            temp_uncertainty = temp_uncertainty.mask(max_outlier_mask, np.nan)
            self.metadata["max_outlier_values"] = list(max_q_value)
            self.metadata["max_outlier_quantile"] = quantile
        if drop_min:
            min_outlier_mask = temp_data.le(min_q_value)
            temp_data = temp_data.mask(min_outlier_mask, np.nan)
            temp_uncertainty = temp_uncertainty.mask(min_outlier_mask, np.nan)
            self.metadata["min_outlier_values"] = list(min_q_value)
            self.metadata["min_outlier_quantile"] = 1.0 - quantile
        temp_data = temp_data.dropna(axis=0)
        temp_uncertainty = temp_uncertainty.dropna(axis=0)
        self._set_dataset(temp_data, temp_uncertainty)
        self.min_values = temp_data.min(axis=0).combine(temp_uncertainty.min(axis=0), min)
        self.max_values = temp_data.max(axis=0).combine(temp_uncertainty.max(axis=0), max)
        self.min_values[self.min_values <= 0] = 0.0

        logger.info(f"Removed outliers for quantile: {quantile}, min values: {drop_min}, max values: {drop_max}")
        logger.info(f"Original row count: {self.input_data.shape[0]}, Updated row count: {temp_data.shape[0]}")

    def scale(self, data=None, min_values=None, max_values=None, min_value=1e-10):
        """
        Min/max scaling
        :return:
        """
        _min_values = (self.min_values.to_numpy() if min_values is None else min_values) + min_value
        _max_values = (self.max_values.to_numpy() if max_values is None else max_values) + min_value
        _data = self.input_data_processed if data is None else data
        scaled_data = (_data - _min_values) / (_max_values - _min_values)
        if data is None:
            scaled_uncertainty = (self.uncertainty_data_processed - _min_values) / (_max_values - _min_values)
            self._set_dataset(scaled_data, scaled_uncertainty)
        else:
            return scaled_data

    def remove_noisy(self, max_sn=1.0):
        _input_data = self.input_data.copy()
        _uncertainty_data = self.uncertainty_data.copy()
        for k, sn in self.metrics["S/N"].items():
            if sn < max_sn:
                _input_data = _input_data.drop(k, axis=1)
                _uncertainty_data = _uncertainty_data.drop(k, axis=1)
        self._set_dataset(_input_data, _uncertainty_data)

    def data_uncertainty_plot(self, feature_selection):
        if type(feature_selection) is int:
            feature_selection = feature_selection % self.input_data.shape[0]
            feature_label = self.input_data.columns[feature_selection]
        else:
            feature_label = feature_selection
        feature_data = self.input_data[feature_label]
        feature_uncertainty = self.uncertainty_data[feature_label]

        du_plot = go.Figure(data=go.Scatter(x=feature_data, y=feature_uncertainty, mode='markers', name=feature_label))
        du_plot.update_layout(title=f"Concentration/Uncertainty Scatter Plot - {feature_label}", width=800, height=600)
        du_plot.show()

    def feature_data_plot(self, x_id, y_id):
        if type(x_id) is int:
            x_id = x_id % self.input_data.shape[0]
            x_label = self.input_data.columns[x_id]
        else:
            x_label = x_id
        if type(y_id) is int:
            y_id = y_id % self.input_data.shape[0]
            y_label = self.input_data.columns[y_id]
        else:
            y_label = y_id
        x_data = self.input_data[x_label]
        y_data = self.input_data[y_label]

        A = np.vstack([x_data.values, np.ones(len(x_data.values))]).T
        m, c = np.linalg.lstsq(A, y_data.values, rcond=None)[0]

        m1, c1 = np.linalg.lstsq(A, x_data.values, rcond=None)[0]

        xy_plot = go.Figure()
        xy_plot.add_trace(go.Scatter(x=x_data, y=y_data, mode='markers', name="Data"))
        xy_plot.add_trace(go.Scatter(x=x_data, y=(m*x_data.values + c), line=dict(color='red', dash='dash', width=1), name='Regression'))
        xy_plot.add_trace(go.Scatter(x=x_data, y=(m1*x_data.values + c1), line=dict(color='blue', width=1), name='One-to-One'))
        xy_plot.update_layout(title=f"{y_label}/{x_label}", width=800, height=600,
                              xaxis_title=f"{x_label}", yaxis_title=f"{y_label}",
                              )
        xy_plot.update_xaxes(range=[0, x_data.max() + 0.5])
        xy_plot.update_yaxes(range=[0, y_data.max() + 0.5])
        xy_plot.show()

    def feature_timeseries_plot(self, feature_selection):
        if type(feature_selection) is int:
            feature_selection = feature_selection % self.input_data.shape[0]
            feature_selection = self.input_data.columns[feature_selection]
            feature_label = [feature_selection]
        else:
            if type(feature_selection) is list:
                feature_label = self.input_data.columns[feature_selection]
            else:
                feature_label = [feature_selection]
        data_df = copy.copy(self.input_data)
        data_df.index = pd.to_datetime(data_df.index)
        data_df = data_df.sort_index()
        data_df = data_df.resample('D').mean()
        x = list(data_df.index)
        ts_plot = go.Figure()
        for feature_i in feature_label:
            y0 = data_df[feature_i]
            y = y0[x]
            ts_plot.add_trace(go.Scatter(x=x, y=y, line=dict(width=1), mode='lines+markers', name=feature_i))
        ts_plot.update_layout(title=f"Concentration Timeseries", width=800, height=600)
        if len(feature_label) == 1:
            ts_plot.update_layout(showlegend=True)
        ts_plot.show()
