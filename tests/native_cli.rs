use std::process::Command;

#[test]
fn native_commands_fail_explicitly_without_supported_build() {
    if cfg!(all(feature = "native-audio", target_os = "macos")) {
        return;
    }
    let result = Command::new(env!("CARGO_BIN_EXE_daw"))
        .arg("devices")
        .output()
        .unwrap();
    assert!(!result.status.success());
    assert!(String::from_utf8_lossy(&result.stderr).contains("requires macOS"));
    assert!(result.stdout.is_empty());
}

#[cfg(all(feature = "native-audio", target_os = "macos"))]
#[test]
fn invalid_native_arguments_fail_before_device_access() {
    for args in [
        vec!["devices", "extra"],
        vec!["play", "missing.json", "invalid"],
    ] {
        let result = Command::new(env!("CARGO_BIN_EXE_daw"))
            .args(args)
            .output()
            .unwrap();
        assert!(!result.status.success());
        assert!(result.stdout.is_empty());
        assert!(!result.stderr.is_empty());
    }
}
