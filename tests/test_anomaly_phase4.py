import pandas as pd

from forecasting import ExpenseAnomaly, detect_anomalies, structured_anomalies


def _rows():
    return pd.DataFrame([
        {"transaction_id": i, "date": pd.Timestamp("2025-01-01") + pd.Timedelta(days=i),
         "category": "Groceries", "description": f"shop {i}",
         "amount_eur": 10.0 if i < 20 else 500.0}
        for i in range(21)
    ])


def test_structured_anomalies_expose_contract_and_reasons():
    records = structured_anomalies(_rows(), contamination=0.05)
    assert records
    assert all(isinstance(item, ExpenseAnomaly) for item in records)
    assert any(item.transaction_id == 20 for item in records)
    assert records[-1].severity in {"low", "medium", "high"}
    assert records[-1].reasons


def test_anomaly_features_do_not_encode_category_as_numeric(monkeypatch):
    seen = {}

    class FakeForest:
        def __init__(self, **kwargs):
            pass

        def fit_predict(self, x):
            seen["columns"] = list(x.columns)
            return [1] * len(x)

        def decision_function(self, x):
            return [0.0] * len(x)

    monkeypatch.setattr("sklearn.ensemble.IsolationForest", FakeForest)
    detect_anomalies(_rows())
    assert "cat_code" not in seen["columns"]
