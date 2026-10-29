from pathlib import Path
import pytest
from agent_fleet.services.ssh_key_service import (
    diagnose_ssh_failure,
    get_controller_public_key,
    is_safe_pub_path,
    read_public_key_file,
)


def test_is_safe_pub_path_restrictions(tmp_path: Path):
    ssh_dir = tmp_path / ".ssh"
    ssh_dir.mkdir()
    
    # 1. Valid pub file inside ssh_dir
    valid_pub = ssh_dir / "id_ed25519.pub"
    valid_pub.write_text("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI... test@controller")
    assert is_safe_pub_path(valid_pub, ssh_dir) is True

    # 2. Private key must be rejected even inside ssh_dir
    priv_key = ssh_dir / "id_ed25519"
    priv_key.write_text("-----BEGIN OPENSSH PRIVATE KEY-----")
    assert is_safe_pub_path(priv_key, ssh_dir) is False

    # 3. Path outside ssh_dir must be rejected (path traversal)
    outside_pub = tmp_path / "outside.pub"
    outside_pub.write_text("ssh-rsa AAAAB3NzaC1yc2E...")
    assert is_safe_pub_path(outside_pub, ssh_dir) is False


def test_read_public_key_file_security(tmp_path: Path):
    ssh_dir = tmp_path / ".ssh"
    ssh_dir.mkdir()
    
    priv_key = ssh_dir / "id_rsa"
    priv_key.write_text("PRIVATE CONTENT")
    
    with pytest.raises(ValueError, match="Security restriction"):
        read_public_key_file(priv_key, ssh_dir)


def test_get_controller_public_key_precedence(tmp_path: Path):
    ssh_dir = tmp_path / ".ssh"
    ssh_dir.mkdir()

    # Create both rsa and ed25519 keys
    rsa_pub = ssh_dir / "id_rsa.pub"
    rsa_pub.write_text("ssh-rsa AAAAB3NzaC1yc2E... user@rsa")
    
    ed_pub = ssh_dir / "id_ed25519.pub"
    ed_pub.write_text("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5... user@ed25519")

    # ed25519 should be preferred
    result = get_controller_public_key(ssh_dir=ssh_dir)
    assert result["found"] is True
    assert result["filename"] == "id_ed25519.pub"
    assert result["key_type"] == "ssh-ed25519"
    assert "user@ed25519" in result["public_key"]


def test_get_controller_public_key_missing_dir(tmp_path: Path):
    non_existent = tmp_path / "non_existent"
    result = get_controller_public_key(ssh_dir=non_existent)
    assert result["found"] is False
    assert "ssh-keygen" in result["instructions"]


def test_diagnose_ssh_failure_scenarios():
    diag_perm = diagnose_ssh_failure("Permission denied (publickey,password).")
    assert diag_perm["category"] == "permission_denied"
    assert "authorized_keys" in diag_perm["suggestion"]

    diag_refused = diagnose_ssh_failure("ssh: connect to host 192.168.1.10 port 22: Connection refused")
    assert diag_refused["category"] == "connection_refused"

    diag_timeout = diagnose_ssh_failure("ssh: connect to host 10.0.0.5 port 2222: Operation timed out")
    assert diag_timeout["category"] == "timeout"

    diag_hostkey = diagnose_ssh_failure("Host key verification failed.")
    assert diag_hostkey["category"] == "host_key_verification_failed"

    diag_unknown = diagnose_ssh_failure("some arbitrary network error")
    assert diag_unknown["category"] == "unknown"
