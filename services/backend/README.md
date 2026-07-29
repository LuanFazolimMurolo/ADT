# ADT Backend

FastAPI backend service for ADT.

## Development

### Setup

```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -e ".[dev]"
```

### Running

```bash
python -m uvicorn app.main:app --reload
```

### Testing

```bash
pytest
pytest --cov
```

### Code Quality

```bash
ruff check .
ruff format .
mypy app
```
