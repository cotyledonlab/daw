use daw::control::MAX_MESSAGE_BYTES;
use serde_json::Value;
use std::{
    io::Write,
    process::{Command, Stdio},
};

fn run(input: Vec<u8>) -> Vec<Value> {
    let mut child = Command::new(env!("CARGO_BIN_EXE_daw"))
        .arg("serve")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    let mut stdin = child.stdin.take().unwrap();
    let writer = std::thread::spawn(move || {
        stdin.write_all(&input).unwrap();
    });
    let output = child.wait_with_output().unwrap();
    writer.join().unwrap();
    assert!(output.status.success(), "{:?}", output);
    assert!(output.stderr.is_empty());
    String::from_utf8(output.stdout)
        .unwrap()
        .lines()
        .map(|line| serde_json::from_str(line).unwrap())
        .collect()
}

#[test]
fn stream_recovers_after_bad_json_utf8_and_oversized_lines() {
    let mut input = b"not json\n\xff\n".to_vec();
    input.extend(vec![b'x'; MAX_MESSAGE_BYTES + 2]);
    input.extend(b"\n{\"protocol_version\":2,\"id\":\"old\",\"method\":\"session.get\"}\n");
    input.extend(b"{\"protocol_version\":1,\"id\":\"last\",\"method\":\"session.get\"}");
    let responses = run(input);
    assert_eq!(responses.len(), 5);
    assert_eq!(responses[0]["error"]["code"], "invalid_request");
    assert_eq!(responses[1]["error"]["code"], "invalid_request");
    assert_eq!(responses[2]["error"]["code"], "request_too_large");
    assert_eq!(responses[3]["error"]["code"], "unsupported_version");
    assert_eq!(responses[3]["id"], "old");
    assert_eq!(responses[4]["id"], "last");
    assert_eq!(responses[4]["result"]["tracks"], serde_json::json!([]));
}

#[test]
fn request_at_size_limit_is_accepted_and_empty_input_exits() {
    assert!(run(vec![]).is_empty());
    let mut input = br#"{"protocol_version":1,"id":"limit","method":"capabilities"}"#.to_vec();
    input.resize(MAX_MESSAGE_BYTES, b' ');
    input.push(b'\n');
    let response = run(input);
    assert_eq!(response.len(), 1);
    assert_eq!(response[0]["ok"], true);
}
