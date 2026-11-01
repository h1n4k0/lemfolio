//! Embed the application icon as Windows resource 1, which GPUI loads for the window class.
//! The .res file is written here so that building needs no resource compiler from a Windows SDK.
use std::{env, fs, path::PathBuf};

fn resource(output: &mut Vec<u8>, kind: u16, id: u16, data: &[u8]) {
    output.extend((data.len() as u32).to_le_bytes());
    output.extend(32u32.to_le_bytes());
    for value in [0xffffu16, kind, 0xffff, id] {
        output.extend(value.to_le_bytes());
    }
    output.extend(0u32.to_le_bytes());
    output.extend(0x1030u16.to_le_bytes());
    output.extend(0x0409u16.to_le_bytes());
    output.extend([0u8; 8]);
    output.extend(data);
    output.resize(output.len().next_multiple_of(4), 0);
}

fn main() {
    println!("cargo:rerun-if-changed=assets/brand/lemfolio.ico");
    println!("cargo:rerun-if-changed=build.rs");
    if env::var("CARGO_CFG_TARGET_OS").as_deref() != Ok("windows") {
        return;
    }
    let icon = fs::read("assets/brand/lemfolio.ico").expect("read application icon");
    let count = u16::from_le_bytes([icon[4], icon[5]]);
    let mut output = vec![0u8; 8];
    output[4] = 32;
    output.extend([0xff, 0xff, 0, 0, 0xff, 0xff, 0, 0]);
    output.resize(32, 0);
    let mut group = vec![0, 0, 1, 0];
    group.extend(count.to_le_bytes());
    for index in 0..count {
        let entry = &icon[6 + index as usize * 16..][..16];
        let size = u32::from_le_bytes(entry[8..12].try_into().unwrap()) as usize;
        let start = u32::from_le_bytes(entry[12..16].try_into().unwrap()) as usize;
        resource(&mut output, 3, index + 1, &icon[start..start + size]);
        group.extend(&entry[..12]);
        group.extend((index + 1).to_le_bytes());
    }
    resource(&mut output, 14, 1, &group);
    let path = PathBuf::from(env::var("OUT_DIR").unwrap()).join("lemfolio.res");
    fs::write(&path, output).expect("write icon resource");
    println!("cargo:rustc-link-arg-bins={}", path.display());
}
