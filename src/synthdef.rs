//! Portable inspection of a bounded, single SCgf-v2 program.
//! Structural validity does not prove installed UGen compatibility or DSP success.
use serde::Serialize;
use std::collections::BTreeSet;

pub const MAX_BYTES: usize = 65_536;
pub const MAX_PARAMETERS: usize = 256;
pub const MAX_CONTROLS: usize = 64;
pub const MAX_UGENS: usize = 1024;
const MAX_CONSTANTS: usize = 4096;
const MAX_PORTS: usize = 16_384;

#[derive(Debug, Serialize)]
pub struct Program {
    pub name: String,
    pub controls: Vec<Control>,
    pub ugen_count: usize,
    #[serde(skip)]
    pub(crate) scalar_parameters: Vec<bool>,
}

#[derive(Debug, Serialize)]
pub struct Control {
    pub name: String,
    pub index: usize,
    pub default_values: Vec<f32>,
}

/// Decode independently of program validation so the protocol can distinguish
/// malformed JSON/hex from a well-encoded but unsupported program.
pub fn decode_hex(hex: &str) -> Result<Vec<u8>, String> {
    if hex.is_empty() || hex.len() > MAX_BYTES * 2 || hex.len() % 2 != 0 {
        return Err("synthdef_hex must encode 1..65536 bytes as even-length hex".into());
    }
    hex.as_bytes()
        .chunks_exact(2)
        .map(|pair| {
            let digit = |byte: u8| match byte {
                b'0'..=b'9' => Some(byte - b'0'),
                b'a'..=b'f' => Some(byte - b'a' + 10),
                b'A'..=b'F' => Some(byte - b'A' + 10),
                _ => None,
            };
            Ok(
                digit(pair[0]).ok_or("synthdef_hex contains a non-hex byte")? * 16
                    + digit(pair[1]).ok_or("synthdef_hex contains a non-hex byte")?,
            )
        })
        .collect()
}

pub fn inspect(bytes: &[u8]) -> Result<Program, String> {
    if bytes.len() > MAX_BYTES {
        return Err("SynthDef exceeds 65536 bytes".into());
    }
    let mut input = Reader { bytes, position: 0 };
    if input.take(4)? != b"SCgf" || input.i32()? != 2 || input.i16()? != 1 {
        return Err("expected SCgf version 2 with exactly one definition".into());
    }
    let name = input.name()?;
    let constants = input.count(MAX_CONSTANTS, "constants")?;
    for _ in 0..constants {
        input.float()?;
    }
    let parameters = input.count(MAX_PARAMETERS, "parameters")?;
    let defaults: Vec<_> = (0..parameters)
        .map(|_| input.float())
        .collect::<Result<_, _>>()?;
    let names = input.count(MAX_CONTROLS, "control names")?;
    let mut controls = Vec::with_capacity(names);
    let mut seen_names = BTreeSet::new();
    let mut indices = BTreeSet::new();
    for _ in 0..names {
        let name = input.name()?;
        let index = input.count(parameters, "control index")?;
        if index >= parameters || !seen_names.insert(name.clone()) || !indices.insert(index) {
            return Err("control names and indices must be unique and within parameters".into());
        }
        controls.push(Control {
            name,
            index,
            default_values: Vec::new(),
        });
    }
    // A named array spans up to the next named index, regardless of the order
    // in which names were serialized. Unnamed parameter prefixes stay unnamed.
    for control in &mut controls {
        let end = indices
            .range(control.index + 1..)
            .next()
            .copied()
            .unwrap_or(parameters);
        control
            .default_values
            .extend_from_slice(&defaults[control.index..end]);
    }
    let ugen_count = input.count(MAX_UGENS, "UGens")?;
    let mut outputs = Vec::with_capacity(ugen_count);
    let mut total_ports = 0usize;
    let mut scalar_parameters = vec![false; parameters];
    for ugen_index in 0..ugen_count {
        let class = input.name()?;
        let calculation_rate = input.rate()?;
        let inputs = input.count(4096, "UGen inputs")?;
        let output_count = input.count(MAX_PARAMETERS, "UGen outputs")?;
        total_ports += inputs + output_count;
        if total_ports > MAX_PORTS {
            return Err("SynthDef exceeds 16384 total input/output ports".into());
        }
        let special = input.i16()?;
        if matches!(
            class.as_str(),
            "Control" | "AudioControl" | "TrigControl" | "LagControl"
        ) && (special < 0 || special as usize + output_count > parameters)
        {
            return Err("control UGen output span exceeds parameter array".into());
        }
        for _ in 0..inputs {
            let source = input.i32()?;
            let index = input.i32()?;
            let limit = if source == -1 {
                constants
            } else if source >= 0 && (source as usize) < ugen_index {
                outputs[source as usize]
            } else {
                return Err("UGen input must reference a constant or an earlier UGen".into());
            };
            if index < 0 || index as usize >= limit {
                return Err("UGen input index exceeds source outputs/constants".into());
            }
        }
        for output in 0..output_count {
            let output_rate = input.rate()?;
            if matches!(
                class.as_str(),
                "Control" | "AudioControl" | "TrigControl" | "LagControl"
            ) {
                scalar_parameters[special as usize + output] |=
                    calculation_rate == 0 || output_rate == 0;
            }
        }
        outputs.push(output_count);
    }
    if input.i16()? != 0 {
        return Err("SynthDef variants are unsupported".into());
    }
    if input.position != bytes.len() {
        return Err("trailing SynthDef bytes are unsupported".into());
    }
    Ok(Program {
        name,
        controls,
        ugen_count,
        scalar_parameters,
    })
}

struct Reader<'a> {
    bytes: &'a [u8],
    position: usize,
}

impl<'a> Reader<'a> {
    fn take(&mut self, length: usize) -> Result<&'a [u8], String> {
        let end = self
            .position
            .checked_add(length)
            .ok_or("SynthDef offset overflow")?;
        let data = self
            .bytes
            .get(self.position..end)
            .ok_or("truncated SynthDef")?;
        self.position = end;
        Ok(data)
    }
    fn i32(&mut self) -> Result<i32, String> {
        Ok(i32::from_be_bytes(self.take(4)?.try_into().unwrap()))
    }
    fn i16(&mut self) -> Result<i16, String> {
        Ok(i16::from_be_bytes(self.take(2)?.try_into().unwrap()))
    }
    fn count(&mut self, maximum: usize, label: &str) -> Result<usize, String> {
        let value = self.i32()?;
        if value < 0 || value as usize > maximum {
            return Err(format!("{label} must be between 0 and {maximum}"));
        }
        Ok(value as usize)
    }
    fn float(&mut self) -> Result<f32, String> {
        let value = f32::from_bits(self.i32()? as u32);
        if !value.is_finite() {
            return Err("SynthDef constants/defaults must be finite".into());
        }
        Ok(value)
    }
    fn name(&mut self) -> Result<String, String> {
        let length = self.take(1)?[0] as usize;
        let bytes = self.take(length)?;
        if bytes.is_empty() || bytes.contains(&0) {
            return Err("SynthDef names must contain 1..255 UTF-8 bytes without NUL".into());
        }
        String::from_utf8(bytes.to_vec()).map_err(|_| "SynthDef name is not UTF-8".into())
    }
    fn rate(&mut self) -> Result<u8, String> {
        let rate = self.take(1)?[0];
        if rate > 3 {
            return Err("UGen calculation rates must be 0..3".into());
        }
        Ok(rate)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hostile_counts_and_every_truncated_prefix_are_rejected() {
        // A valid program with no controls or UGens needs no runtime to inspect.
        let mut program = b"SCgf\0\0\0\x02\0\x01\x01x".to_vec();
        program.extend_from_slice(&[0; 18]);
        assert!(inspect(&program).is_ok());
        for length in 0..program.len() {
            assert!(inspect(&program[..length]).is_err());
        }
        program[12..16].copy_from_slice(&i32::MAX.to_be_bytes());
        assert!(inspect(&program).is_err());
    }

    #[test]
    fn hex_decoding_rejects_bad_encoding_and_oversized_input() {
        assert_eq!(decode_hex("0aFF").unwrap(), vec![10, 255]);
        for invalid in ["", "0", "gg", "é", " 0"] {
            assert!(decode_hex(invalid).is_err());
        }
        assert!(decode_hex(&"00".repeat(MAX_BYTES + 1)).is_err());
    }
}
