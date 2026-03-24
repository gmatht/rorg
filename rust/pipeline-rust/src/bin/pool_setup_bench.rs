use rayon::ThreadPoolBuilder;
use std::env;
use std::time::Instant;

fn main() {
    let args: Vec<String> = env::args().collect();
    if args.len() < 3 {
        eprintln!("usage: pool_setup_bench <iterations> <jobs...>");
        std::process::exit(2);
    }
    let iterations: usize = args[1].parse().expect("iterations must be usize");
    let jobs_values: Vec<usize> = args[2..]
        .iter()
        .map(|s| s.parse().expect("jobs must be usize"))
        .collect();
    for jobs in jobs_values {
        let mut wall_ms = Vec::with_capacity(iterations);
        for _ in 0..iterations {
            let wall = Instant::now();
            let _pool = ThreadPoolBuilder::new()
                .num_threads(jobs)
                .build()
                .expect("failed to build pool");
            wall_ms.push(wall.elapsed().as_secs_f64() * 1000.0);
        }
        println!(
            "{}: wall_ms={}",
            jobs,
            wall_ms
                .iter()
                .map(|v| format!("{v:.6}"))
                .collect::<Vec<_>>()
                .join(","),
        );
    }
}
