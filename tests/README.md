# Running the Tests

The unit tests require several third-party packages which are not installed by default. Install the packages in `requirements.txt` along with the following before running `pytest`:

- `pypdf`
- `torch`
- `numpy`
- `requests`
- `boto3` (installs `botocore`)

You can install everything with pip in a clean environment:

```bash
pip install -r ../requirements.txt
```

Then run the tests:

```bash
pytest
```
