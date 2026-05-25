//! Smoke test: prove the `sqlite-vec` extension actually loads on this build.
//! If this fails, the rest of R3 is moot.

#[test]
fn sqlite_vec_loads_on_memory_connection() {
    // Register sqlite-vec as an auto-extension so subsequent Connection::open
    // calls automatically load it. The sqlite-vec crate exposes
    // `sqlite3_vec_init` as the C-callable init function.
    unsafe {
        let _ = rusqlite::ffi::sqlite3_auto_extension(Some(std::mem::transmute(
            sqlite_vec::sqlite3_vec_init as *const (),
        )));
    }

    let conn = rusqlite::Connection::open_in_memory().expect("open :memory:");

    // Verify a vec0 virtual table can be created.
    conn.execute_batch(
        "CREATE VIRTUAL TABLE smoke_vec USING vec0(id TEXT PRIMARY KEY, embedding FLOAT[3])",
    )
    .expect("create vec0 table");

    // Insert + query a single vector.
    conn.execute(
        "INSERT INTO smoke_vec (id, embedding) VALUES ('a', ?)",
        rusqlite::params!["[1.0, 0.0, 0.0]"],
    )
    .expect("insert vector");

    let mut stmt = conn
        .prepare("SELECT id FROM smoke_vec WHERE embedding MATCH ? AND k = 1")
        .expect("prepare match");
    let rows: Vec<String> = stmt
        .query_map(rusqlite::params!["[1.0, 0.0, 0.0]"], |r| {
            r.get::<_, String>(0)
        })
        .expect("query")
        .map(|r| r.unwrap())
        .collect();
    assert_eq!(rows, vec!["a".to_string()]);
}
