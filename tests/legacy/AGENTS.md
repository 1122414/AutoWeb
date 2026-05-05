# Test Knowledge Base

Mixed test suite + demo scripts — unittest framework.

## STRUCTURE

```
test/
├── test_*.py              # Unit tests (unittest framework)
├── auto_crawler_demo/     # Crawler demonstration scripts
├── mcp_learn/            # MCP learning examples
├── milvus_relation/      # Milvus relationship tests
├── check_*.py            # Environment checks
└── test.html             # Test HTML fixture
```

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| Code cache tests | `test_code_cache_v6.py` | CodeCache validation |
| DOM compressor tests | `test_dom_compressor.py` | DOM compression tests |
| Verification tests | `test_verification_contract.py` | Verifier logic tests |
| Environment checks | `check_milvus.py`, `check_env.py` | Manual validation scripts |

## CONVENTIONS

- **Framework**: `unittest` (no pytest config in this project)
- **Naming**: `test_*.py` for test files, `Test*` classes, `test_*` methods
- **Mixed content**: Directory contains both tests AND demo utilities
- **Run tests**: `python -m unittest discover -s test -p "test_*.py"`

## NOTES

- Not all files are tests — some are standalone utilities (fibonacci.py, get_browser_data.py)
- Both `test/` and `tests/` exist; `test/` is the primary source directory
