from services.dataset_intelligence.validation import validate_upload


def test_empty_file_rejected():
    result = validate_upload("empty.csv", b"")
    assert not result.ok
    assert any(i.code == "empty_file" for i in result.error_issues())


def test_unsupported_extension_rejected():
    result = validate_upload("data.exe", b"not a real dataset")
    assert not result.ok
    assert any(i.code == "unsupported_extension" for i in result.error_issues())


def test_oversized_file_rejected():
    from core.config import get_settings
    max_bytes = get_settings().dataset_max_upload_mb * 1024 * 1024
    oversized = b"x" * (max_bytes + 1)
    result = validate_upload("big.csv", oversized)
    assert not result.ok
    assert any(i.code == "file_too_large" for i in result.error_issues())


def test_valid_csv_passes():
    content = b"a,b,c\n1,2,3\n4,5,6\n"
    result = validate_upload("good.csv", content)
    assert result.ok
    assert result.detected_delimiter == ","


def test_duplicate_headers_warn_but_pass():
    content = b"a,a,b\n1,2,3\n"
    result = validate_upload("dupes.csv", content)
    assert result.ok  # warning, not error
    assert any(i.code == "duplicate_headers" for i in result.issues)


def test_corrupted_json_rejected():
    result = validate_upload("bad.json", b"{not valid json")
    assert not result.ok
    assert any(i.code == "corrupted_file" for i in result.error_issues())


def test_valid_json_array_passes():
    content = b'[{"a": 1, "b": 2}, {"a": 3, "b": 4}]'
    result = validate_upload("good.json", content)
    assert result.ok


def test_xlsx_bad_signature_rejected():
    result = validate_upload("fake.xlsx", b"this is not a real xlsx file")
    assert not result.ok
    assert any(i.code == "corrupted_file" for i in result.error_issues())


def test_never_raises_on_garbage_input():
    """Step 1's core promise: never crash, always return a structured result."""
    garbage_inputs = [b"\x00\x01\x02\xff\xfe", b"", b"a" * 100, "unicode-héllo".encode()]
    for content in garbage_inputs:
        for ext_name in ["file.csv", "file.json", "file.xlsx", "file.tsv"]:
            result = validate_upload(ext_name, content)
            assert result is not None  # never raises, always returns something
