use std::io::{self, Read};

fn main() {
    let mut input = String::new();
    if io::stdin().take(4 * 1024 * 1024 + 1).read_to_string(&mut input).is_err()
        || input.len() > 4 * 1024 * 1024
    {
        println!("{{\"status\":\"invalid\",\"error\":\"Input exceeds 4 MiB or is not UTF-8\"}}");
        return;
    }
    let request: serde_json::Value = match serde_json::from_str(&input) {
        Ok(value) => value,
        Err(_) => {
            println!("{{\"status\":\"invalid\",\"error\":\"Expected a JSON diff request\"}}");
            return;
        }
    };
    let Some(unified) = request.get("unified").and_then(|x| x.as_str()) else {
        println!("{{\"status\":\"invalid\",\"error\":\"Missing unified diff text\"}}");
        return;
    };
    let context = request.get("context").and_then(|x| x.as_u64()).unwrap_or(3).min(100) as usize;
    println!("{}", lemfolio_diff::describe(unified, context));
}
