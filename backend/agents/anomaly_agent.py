"""Agent 2: independent anomaly detection.

Trained on BENIGN flows only, with its own preprocessing. It imports nothing from Agent 1 and
sees neither classifier outputs nor attack labels when scoring.

anomaly_score is an empirical percentile: the share of held-out benign calibration flows that
looked less unusual than this flow. 0.97 means "more unusual than 97% of benign calibration
flows". It is not the probability that a flow is an attack.
"""
from __future__ import annotations

from dataclasses import dataclass

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.neighbors import NearestNeighbors

# RobustBaseline must stay importable from this module: the saved model was pickled against this path.
from backend.intelligence.feature_explainer import RobustBaseline, explain_deviations
from backend.preprocessing.data_processor import PORT_FEATURE, FeaturePipeline
from backend.schemas.contracts import AnomalyAssessment

TOP_K_FOR_SCORE = 5
NEIGHBOURS = 3
NEIGHBOUR_SAMPLE = 80_000  # Benign flows kept for the distance check. Chosen on the selection split.


@dataclass
class ScaledEnsemble:
    """Two benign-only views on a log scale: how easily a flow is isolated, and how far it is from known benign flows.

    Raw flow features span many orders of magnitude, so a few huge values hide everything else. A log scale lets
    small, odd flows such as port scans and brute-force attempts stand out as well. Neither view sees an attack label.
    """

    median: np.ndarray
    spread: np.ndarray
    forest: IsolationForest
    neighbours: NearestNeighbors
    forest_reference: np.ndarray | None = None
    neighbour_reference: np.ndarray | None = None
    reference: np.ndarray | None = None

    @classmethod
    def fit(cls, X: np.ndarray, seed: int) -> "ScaledEnsemble":
        logged = np.sign(X) * np.log1p(np.abs(X))
        median = np.median(logged, axis=0)
        spread = np.subtract(*np.percentile(logged, [75, 25], axis=0))
        spread[spread < 1e-6] = 1.0
        Z = ((logged - median) / spread).astype(np.float32)
        forest = IsolationForest(n_estimators=300, max_samples=min(8192, len(Z)), n_jobs=-1, random_state=seed).fit(Z)
        keep = np.random.default_rng(seed).choice(len(Z), min(NEIGHBOUR_SAMPLE, len(Z)), replace=False)
        return cls(median, spread, forest, NearestNeighbors(n_neighbors=NEIGHBOURS, n_jobs=-1).fit(Z[keep]))

    def _views(self, X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        Z = (((np.sign(X) * np.log1p(np.abs(X))) - self.median) / self.spread).astype(np.float32)
        return -self.forest.score_samples(Z), self.neighbours.kneighbors(Z)[0][:, NEIGHBOURS - 1]

    def _combined(self, forest_raw: np.ndarray, neighbour_raw: np.ndarray) -> np.ndarray:
        return (np.searchsorted(self.forest_reference, forest_raw, side="left") / len(self.forest_reference)
                + np.searchsorted(self.neighbour_reference, neighbour_raw, side="left") / len(self.neighbour_reference)) / 2

    def calibrate(self, X: np.ndarray) -> None:
        forest_raw, neighbour_raw = self._views(X)
        self.forest_reference, self.neighbour_reference = np.sort(forest_raw), np.sort(neighbour_raw)
        self.reference = np.sort(self._combined(forest_raw, neighbour_raw))

    def score(self, X: np.ndarray) -> np.ndarray:
        return np.searchsorted(self.reference, self._combined(*self._views(X)), side="left") / len(self.reference)


@dataclass
class AnomalyAgent:
    forest: IsolationForest
    pipeline: FeaturePipeline
    baseline: RobustBaseline
    forest_reference: np.ndarray | None = None
    robust_reference: np.ndarray | None = None
    combined_reference: np.ndarray | None = None
    detector: str = "combined"
    threshold: float = 0.95
    model_version: str = ""
    ensemble: ScaledEnsemble | None = None

    @classmethod
    def train(cls, benign_features: pd.DataFrame, seed: int = 42, n_estimators: int = 200,
              max_samples: int = 4096, model_version: str = "") -> "AnomalyAgent":
        pipeline = FeaturePipeline(exclude=(PORT_FEATURE,)).fit(benign_features)
        X = pipeline.transform(benign_features)
        forest = IsolationForest(n_estimators=n_estimators, max_samples=min(max_samples, len(X)),
                                 n_jobs=-1, random_state=seed).fit(X)
        baseline = RobustBaseline.fit(X, pipeline.columns_)
        return cls(forest, pipeline, baseline, model_version=model_version,
                   ensemble=ScaledEnsemble.fit(X.astype(np.float64), seed))

    def _raw(self, X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        forest_raw = -self.forest.score_samples(X)
        k = min(TOP_K_FOR_SCORE, X.shape[1])
        top = np.partition(np.abs(self.baseline.z(X)), -k, axis=1)[:, -k:]
        return forest_raw, top.mean(axis=1)

    def calibrate(self, benign_features: pd.DataFrame, target_fpr: float, detector: str | None = None) -> None:
        """Build percentile references and the threshold from benign flows unseen in training."""
        forest_raw, robust_raw = self._raw(self.pipeline.transform(benign_features))
        self.forest_reference = np.sort(forest_raw)
        self.robust_reference = np.sort(robust_raw)
        self.combined_reference = np.sort(self._combine(forest_raw, robust_raw))
        if self.ensemble is not None:
            self.ensemble.calibrate(self.pipeline.transform(benign_features).astype(np.float64))
        self.threshold = 1.0 - target_fpr
        if detector:
            self.detector = detector

    def _percentile(self, raw: np.ndarray, reference: np.ndarray) -> np.ndarray:
        return np.searchsorted(reference, raw, side="left") / len(reference)

    def _combine(self, forest_raw: np.ndarray, robust_raw: np.ndarray) -> np.ndarray:
        return (self._percentile(forest_raw, self.forest_reference)
                + self._percentile(robust_raw, self.robust_reference)) / 2

    def score_detectors(self, features: pd.DataFrame) -> dict[str, np.ndarray]:
        X = self.pipeline.transform(features)
        forest_raw, robust_raw = self._raw(X)
        scores = {
            "isolation_forest": self._percentile(forest_raw, self.forest_reference),
            "robust_deviation": self._percentile(robust_raw, self.robust_reference),
            "combined": self._percentile(self._combine(forest_raw, robust_raw), self.combined_reference),
        }
        if self.ensemble is not None:
            scores["scaled_ensemble"] = self.ensemble.score(X.astype(np.float64))
        return scores

    def score(self, features: pd.DataFrame) -> np.ndarray:
        if self.detector == "scaled_ensemble":
            return self.ensemble.score(self.pipeline.transform(features).astype(np.float64))
        return self.score_detectors(features)[self.detector]

    def supporting_features(self, features: pd.DataFrame, top: int = 5) -> list[list[dict]]:
        return explain_deviations(self.baseline, self.pipeline.transform(features), top)

    def to_output(self, flow_id: str, score: float, supporting: list[dict]) -> dict:
        return AnomalyAssessment(
            flow_id=flow_id,
            anomaly_score=float(score),
            score_meaning="percentile among held-out benign calibration flows; not a probability",
            is_anomaly=bool(score >= self.threshold),
            threshold=self.threshold,
            supporting_features=supporting,
            baseline_reference="benign_training_distribution",
            model_version=self.model_version,
        ).model_dump()

    def save(self, path) -> None:
        joblib.dump(self, path, compress=3)

    @staticmethod
    def load(path) -> "AnomalyAgent":
        return joblib.load(path)
