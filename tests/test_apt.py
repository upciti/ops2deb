import pytest

from ops2deb.apt import sync_list_repository_packages
from ops2deb.exceptions import Ops2debAptError


def test_sync_list_repository_packages__should_raise_exception_when_http_error_occurs(
    mock_httpx_client,
):
    with pytest.raises(Ops2debAptError, match="Failed to download APT repository file"):
        sync_list_repository_packages("http://test.com/not_found stable")


def test_sync_list_repository_packages__should_raise_exception_when_repo_url_is_invalid(  # noqa: E501
    mock_httpx_client,
):
    with pytest.raises(Ops2debAptError, match="invalid or missing URL scheme"):
        sync_list_repository_packages("invalid-url strable")


def test_sync_list_repository_packages__should_raise_exception_when_repo_distribution_is_missing(  # noqa: E501
    mock_httpx_client,
):
    with pytest.raises(Ops2debAptError, match="The expected format for the"):
        sync_list_repository_packages("http://deb.wakemeops.com/")
