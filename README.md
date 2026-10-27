# my_redis_server

A **Redis-compatible in-memory data store written in C++17**. It speaks the Redis Serialization Protocol (RESP) over plain TCP, so the standard `redis-cli` and `redis-benchmark` tools work against it unchanged. It supports strings, lists and hashes, key expiry (TTL), multiple concurrent clients, request pipelining and snapshot persistence to disk.

> **Project history:** the base server was built as a guided project. The fixes, protocol work, data-structure change and benchmarking described in [Changes and fixes](#changes-and-fixes) and [Benchmark](#benchmark) were added on top of it.

## Contents

1. [Highlights](#highlights)
2. [Features](#features)
3. [Supported commands](#supported-commands)
4. [Architecture](#architecture)
5. [Changes and fixes](#changes-and-fixes)
6. [Benchmark](#benchmark)
7. [Build and run](#build-and-run)
8. [Testing](#testing)
9. [Project structure](#project-structure)
10. [Known limitations and roadmap](#known-limitations-and-roadmap)

---

## Highlights

- **29 commands** across strings, lists and hashes, plus TTL, `KEYS`, `TYPE`, `RENAME` and more.
- **Works with real Redis tooling:** `redis-cli` and `redis-benchmark` connect directly.
- **Multi-client:** one thread per connection with a single mutex guarding the data.
- **Pipelining and large commands:** per-connection buffering, so many commands in one packet, or one command split over many packets, both work.
- **Safe persistence:** length-prefixed snapshot, written atomically, with TTLs preserved.
- **Robust parser:** malformed input closes that one connection and never crashes the server.
- **Measured performance:** about **38-41 % of Redis 8.0.5 throughput** on the same machine (10 clients, median of 5 runs), after a `std::vector` to `std::deque` change that made list operations **8-11x faster**.

---

## Features

| Area | What is implemented |
|---|---|
| **Data types** | Strings, lists, hashes |
| **Expiry** | `EXPIRE` sets a TTL (works on strings, lists and hashes; `RENAME` carries the TTL along). Expired keys are purged lazily, see [Known limitations](#known-limitations-and-roadmap) |
| **Protocol** | RESP arrays of bulk strings (what `redis-cli` sends) and inline commands; replies use simple strings, errors, integers, bulk strings and arrays |
| **Concurrency** | One `std::thread` per client; one `std::mutex` makes every command atomic |
| **Pipelining** | Any number of commands per `recv()`, commands split across packets, values far larger than a single packet |
| **Persistence** | Snapshot to `dump.my_rdb` every 300 s and on `Ctrl+C`; reloaded at startup |
| **Input safety** | Non-throwing parser with limits (see [Protocol details](#protocol-details)) |

---

## Supported commands

### General

| Command | Syntax | Reply |
|---|---|---|
| `PING` | `PING` | `PONG` |
| `ECHO` | `ECHO <msg>` | the message |
| `FLUSHALL` | `FLUSHALL` | `OK`; removes all keys, lists, hashes and TTLs |

### Keys and strings

| Command | Syntax | Notes |
|---|---|---|
| `SET` | `SET <key> <value>` | Overwrites a key of **any** type and clears its TTL |
| `GET` | `GET <key>` | Value, or nil |
| `KEYS` | `KEYS <pattern>` | Returns **all** keys (the pattern is currently ignored) |
| `TYPE` | `TYPE <key>` | `string`, `list`, `hash` or `none` |
| `DEL` / `UNLINK` | `DEL <key>` | One key; replies `1` if deleted, `0` if absent |
| `EXPIRE` | `EXPIRE <key> <seconds>` | `OK`, or an error if the key does not exist |
| `RENAME` | `RENAME <old> <new>` | Works for any key type and moves the TTL with the key |

### Lists

| Command | Syntax | Notes |
|---|---|---|
| `LPUSH` / `RPUSH` | `LPUSH <key> <v1> [v2 ...]` | Replies with the new length. Values are pushed one at a time |
| `LPOP` / `RPOP` | `LPOP <key>` | Value, or nil when empty |
| `LLEN` | `LLEN <key>` | Length |
| `LGET` | `LGET <key>` | All elements (this server's equivalent of `LRANGE 0 -1`) |
| `LINDEX` | `LINDEX <key> <index>` | Negative indexes count from the end |
| `LSET` | `LSET <key> <index> <value>` | Error if the index is out of range |
| `LREM` | `LREM <key> <count> <value>` | `count > 0` from head, `< 0` from tail, `0` all; replies with the number removed |

### Hashes

| Command | Syntax | Notes |
|---|---|---|
| `HSET` | `HSET <key> <f1> <v1> [f2 v2 ...]` | Stores every pair atomically; replies with the number of pairs given |
| `HMSET` | `HMSET <key> <f1> <v1> [f2 v2 ...]` | Same storage behavior; replies `OK` |
| `HGET` | `HGET <key> <field>` | Value, or nil |
| `HEXISTS` | `HEXISTS <key> <field>` | `1` / `0` |
| `HDEL` | `HDEL <key> <field>` | `1` if removed, `0` otherwise |
| `HLEN` | `HLEN <key>` | Number of fields |
| `HKEYS` / `HVALS` | `HKEYS <key>` | All fields / all values |
| `HGETALL` | `HGETALL <key>` | Field and value pairs |

Errors are returned as `-Error: <message>`; an unknown command returns `-Error: Unknown command`.

---

## Architecture

```
   redis-cli / redis-benchmark / any RESP client
                    |
                    |  TCP (default port 6379)
                    v
 +----------------------------------------------------------+
 |  RedisServer                                             |
 |   accept() loop -> one std::thread per connection        |
 |   per-connection receive buffer ("pending")              |
 |   framing: RedisCommandHandler::commandLength()          |
 |   send loop (partial writes, MSG_NOSIGNAL)               |
 +---------------------------+------------------------------+
                             |  one complete command (string)
                             v
 +----------------------------------------------------------+
 |  RedisCommandHandler                                     |
 |   parseRespCommand() -> tokens (never throws)            |
 |   dispatch on command name -> handler function           |
 |   builds the RESP reply                                  |
 +---------------------------+------------------------------+
                             |  typed calls (set, lpush, hset, ...)
                             v
 +----------------------------------------------------------+
 |  RedisDatabase  (singleton)                              |
 |   std::mutex db_mutex  -- guards everything below        |
 |   kv_store    unordered_map<string, string>              |
 |   list_store  unordered_map<string, deque<string>>       |
 |   hash_store  unordered_map<string, unordered_map<..>>   |
 |   expiry_map  unordered_map<string, time_point>          |
 |   purgeExpired()  lazy eviction                          |
 |   dump() / load()  snapshot persistence                  |
 +---------------------------+------------------------------+
                             ^
        background thread (every 300 s) and Ctrl+C handler
                             |
                       dump.my_rdb
```

### Components

| File | Responsibility |
|---|---|
| `src/main.cpp` | Reads the port argument, loads `dump.my_rdb`, starts the 5-minute persistence thread, runs the server |
| `src/RedisServer.cpp` | Socket setup, accept loop, per-client thread, buffering, framing, replies, `SIGINT` shutdown |
| `src/RedisCommandHandler.cpp` | RESP parsing, `commandLength()` framing helper, command dispatch and reply formatting |
| `src/RedisDatabase.cpp` | The data stores, expiry, thread safety, snapshot dump and load |

### Request lifecycle

1. A client thread calls `recv()` and appends the bytes to that connection's `pending` buffer.
2. `commandLength()` inspects the buffer and answers one of: *a complete command of N bytes*, *not complete yet*, or *protocol error*.
3. For each complete command, `processCommand()` parses it, dispatches to the matching handler and calls the database, which takes `db_mutex` for the duration of that operation.
4. All replies for the batch are concatenated and written back with a send loop that handles partial writes.
5. On a protocol error the client receives `-ERR Protocol error` and only that connection is closed.

### Data structures and cost

| Structure | Used for | Cost |
|---|---|---|
| `unordered_map<string,string>` | Strings | `GET`, `SET`, `DEL`: O(1) average |
| `unordered_map<string,deque<string>>` | Lists | `LPUSH`, `RPUSH`, `LPOP`, `RPOP`, `LINDEX`, `LSET`, `LLEN`: **O(1)**; `LREM`, `LGET`: O(n) |
| `unordered_map<string,unordered_map<string,string>>` | Hashes | Field operations: O(1) average |
| `unordered_map<string,time_point>` | TTLs | Checked by `purgeExpired()` |

Lists use `std::deque` because a `std::vector` has to shift every element on a push or pop at the front; see [Changes and fixes](#changes-and-fixes).

### Concurrency model

- Each accepted connection gets its own `std::thread`; the database is a singleton shared by all of them.
- A single `db_mutex` is held for the whole of each database call, so every command is atomic and the stores are never accessed concurrently. Multi-value `LPUSH`/`RPUSH` is the exception: each value is pushed under its own lock.
- Expiry is **lazy**: `purgeExpired()` removes every key whose TTL has passed, and runs at the start of `GET`, `KEYS`, `TYPE`, `DEL`, `EXPIRE`, `RENAME` and each snapshot. There is no timer thread.
- A detached background thread writes a snapshot every 300 seconds. `Ctrl+C` also writes one before the process exits.

### Persistence format (`dump.my_rdb`, version 2)

Every string is length-prefixed (`<byte-length>:<bytes>`), so keys and values may contain spaces, newlines, `:` or be empty.

```
MYRDB2
K <key> <value>                        string
L <key> <count> <item> ...             list
H <key> <count> <field> <value> ...    hash
E <key> <ms_remaining>                 TTL for <key>
```

- The snapshot is written to `dump.my_rdb.tmp` and then renamed, so a crash during a dump cannot destroy the previous good file.
- `load()` parses into temporary maps and swaps them in only if the whole file was valid, so a corrupt file never leaves a half-loaded database.
- TTLs are stored as remaining milliseconds and re-armed on load.
- Dumps from the earlier version-1 format are not loadable (the server starts empty).

### Protocol details

- **Requests:** RESP arrays of bulk strings, or inline commands (`PING\r\n`).
- **Limits:** up to 1,048,576 arguments per command and 512 MB per argument; inline commands up to 64 KB. Anything outside these limits or malformed (`*abc`, negative lengths, missing `$`) is a protocol error.
- **Pipelining:** supported; replies come back in request order.

---

## Changes and fixes

Improvements made on top of the original guided implementation:

| # | Problem found | Fix | Where |
|---|---|---|---|
| 1 | **Server crash on malformed input.** `std::stoi` threw an uncaught exception in a client thread, which called `std::terminate` and killed the whole server (for example `*abc\r\n`). | Non-throwing integer parser, validated lengths, size limits; a bad request closes only its own connection. | `RedisCommandHandler.cpp` |
| 2 | **Persistence corrupted data.** The dump was space-delimited, so `SET greeting "hello world"` reloaded as `hello`; empty values broke; TTLs were lost. | Length-prefixed v2 format, TTLs saved, atomic write via temp file and rename, load-then-swap. | `RedisDatabase.cpp` |
| 3 | **No request framing.** One `recv()` was treated as one command: pipelined commands got a single reply (clients hung), commands over 1024 bytes broke, and partial `send()` was not handled. | Per-connection buffer and `commandLength()`; multiple commands per read; send loop; `MSG_NOSIGNAL` so a disconnecting client cannot kill the server with `SIGPIPE`. | `RedisServer.cpp`, `RedisCommandHandler.cpp/.h` |
| 4 | **`SET` left stale state.** An old TTL kept deleting the new value, and the key could exist as two types. | `SET` now clears the TTL and removes the key from the list and hash stores. | `RedisDatabase.cpp` |
| 5 | **`HSET` ignored extra pairs.** `HSET k f1 v1 f2 v2` stored only the first pair. | Stores every pair atomically through `hmset`. | `RedisCommandHandler.cpp` |
| 6 | **Slow lists.** `LPUSH`/`LPOP` were O(n) on a `std::vector` (every element shifts). | Lists stored in `std::deque`: O(1) at both ends. | `RedisDatabase.h/.cpp` |

Earlier correctness fixes covered return values for `DEL` and complete cleanup in `FLUSHALL`.

---

## Benchmark

### Method

- **Tool:** `redis-benchmark` 8.0.5, the same binary against both servers.
- **Command:** `redis-benchmark -p <port> -c 10 -n 100000 -t set,get,lpush,lpop -P 1 -q`
  - 10 concurrent clients, 100,000 requests per test, `-P 1` means no pipelining (one request in flight per client).
- **Runs:** 5 runs per configuration; the **median** is reported.
- **Baseline:** real Redis 8.0.5 on the same machine with snapshots off (`save ""`).
- **Environment:** Windows with WSL2 (Ubuntu); server built with `g++ 15.2 -O2`. Machine: `<CPU model, cores, RAM>`.

### Results: compared with Redis

Throughput in requests per second (median of 5 runs).

| Command | Redis 8.0.5 | my_redis_server | Share of Redis | p50 latency (mine / Redis) |
|---|---:|---:|---:|---:|
| `SET` | 189,036 | 71,633 | 38 % | 0.079 ms / 0.031 ms |
| `GET` | 188,324 | 73,910 | 39 % | 0.079 ms / 0.031 ms |
| `LPUSH` | 191,205 | 78,125 | 41 % | 0.079 ms / 0.031 ms |
| `LPOP` | 186,220 | 76,046 | 41 % | 0.079 ms / 0.031 ms |

### Results: the list optimization (`std::vector` to `std::deque`)

| Command | Before (`vector`) | After (`deque`) | Speedup | p50 latency |
|---|---:|---:|---:|---:|
| `LPUSH` | 9,763 req/s | 78,125 req/s | **8.0x** | 1.02 ms to 0.079 ms |
| `LPOP` | 7,045 req/s | 76,046 req/s | **10.8x** | 1.38 ms to 0.079 ms |

`redis-benchmark` pushes every element onto one shared list, which grows to 100,000 entries. With a `std::vector`, each front insert or pop moved all of them, so cost grew with list length. With a `std::deque` it is constant, which is why lists went from about 4-5 % of Redis to the same 41 % as every other command.

### Notes on reading the numbers

- Run-to-run variation is about ±10 % (for example `SET` ranged from 66,050 to 81,103 across five runs), so quote medians rather than the best run.
- `SET` and `GET` were measured on the `vector` build; the `deque` change does not touch those code paths.
- The before and after list measurements were taken in separate sessions on the same machine.
- The remaining gap to Redis comes from the design rather than from any one operation. Likely contributors, not yet profiled: one thread per client, a single global mutex, one `recv`/`send` per request, and string copies.
- Behavior with more than 10 clients has not been benchmarked yet.

### Reproduce

```bash
# terminal 1: your server on 6382 (real Redis stays on 6379)
mkdir -p ~/rt && cd ~/rt && /path/to/my_redis_server 6382

# terminal 2
redis-cli -p 6379 config set save ""        # baseline: no background snapshots
for i in 1 2 3 4 5; do
  redis-benchmark -p 6382 -c 10 -n 100000 -t set,get,lpush,lpop -P 1 -q
done
for i in 1 2 3 4 5; do
  redis-benchmark -p 6379 -c 10 -n 100000 -t set,get,lpush,lpop -P 1 -q
done
```

The `WARNING: Could not fetch server CONFIG` line is expected: this server does not implement `CONFIG`, and `redis-benchmark` carries on. With the pipelining fix you can also try `-P 16`.

---

## Build and run

**Requirements:** a C++17 compiler (`g++`), `make`, and a POSIX system (Linux, macOS, or WSL on Windows). Native Windows is not supported because the server uses POSIX sockets.

```bash
make            # builds ./my_redis_server  (also: make clean, make rebuild, make run)
./my_redis_server            # listens on 6379
./my_redis_server 6380       # custom port
```

Or without `make`:

```bash
g++ -std=c++17 -pthread -O2 -Iinclude src/*.cpp -o my_redis_server
```

On startup the server tries to load `dump.my_rdb` from the **current directory**:

```
Database Loaded From dump.my_rdb
# or
No dump found or load failed; starting with an empty database.
```

Press **Ctrl+C** (not `kill -9`) to stop the server so it writes a final snapshot.

### Using it

```bash
redis-cli -p 6379
127.0.0.1:6379> SET foo "hello world"
OK
127.0.0.1:6379> GET foo
"hello world"
127.0.0.1:6379> RPUSH jobs a b c
(integer) 3
127.0.0.1:6379> LGET jobs
1) "a"
2) "b"
3) "c"
127.0.0.1:6379> HSET user name Alice age 30
(integer) 2
127.0.0.1:6379> EXPIRE foo 60
OK
```

Pipelining check (two replies from one write):

```bash
printf '*1\r\n$4\r\nPING\r\n*1\r\n$4\r\nPING\r\n' | nc -q1 localhost 6379
```

---

## Testing

Two Python scripts (standard library only) live in `tests/`.

**1. Integration tests: 34 checks** (`tests/test_server.py`): malformed input, pipelining (5 and 2,000 commands), commands split across packets, a 300 KB value, `SET` clearing TTL and types, command regressions, and persistence including spaces, newlines, empty values and TTLs surviving a restart.

```bash
mkdir -p ~/rt && cd ~/rt
/path/to/my_redis_server 6382            # terminal 1
python3 tests/test_server.py main 6382   # terminal 2: expect 24 passed
# stop the server with Ctrl+C (writes the dump), start it again, then:
python3 tests/test_server.py after_restart 6382   # expect 10 passed
```

**2. List differential test** (`tests/test_lists.py`): runs 3,000 random list operations (`LPUSH`, `RPUSH`, `LPOP`, `RPOP`, `LREM` with positive, negative and zero counts, `LSET`, `LINDEX`, `LLEN`, `LGET`) and compares every result with an independent Python model. It also prints a hash of all replies, so two builds can be checked for identical behavior.

```bash
python3 tests/test_lists.py 6382 1       # expect: model mismatches=0
```

> `test_lists.py` runs `FLUSHALL`. Do not run it between the `main` and `after_restart` steps above.

`test_all.sh` is an older smoke script that prints command output for manual inspection (it has no assertions).

---

## Project structure

```
.
├── include/
│   ├── RedisCommandHandler.h
│   ├── RedisDatabase.h
│   └── RedisServer.h
├── src/
│   ├── main.cpp
│   ├── RedisCommandHandler.cpp
│   ├── RedisDatabase.cpp
│   └── RedisServer.cpp
├── tests/
│   ├── test_server.py        # 34 integration checks
│   └── test_lists.py         # randomized differential list test
├── Concepts,UseCases&Tests.md   # design concepts and command use cases
├── Makefile
├── test_all.sh               # manual smoke script
└── README.md
```

See [Concepts,UseCases&Tests.md](Concepts,UseCases&Tests.md) for background on TCP sockets, RESP and the data structures.

---

## Known limitations and roadmap

**Current limitations**

- **Scalability:** one thread per client and a single global mutex. Throughput at higher client counts has not been measured and is expected to degrade.
- **Expiry is only partly enforced on reads:** because purging is triggered by the commands listed above, a list or hash whose TTL has passed can still be read with `LGET`, `LLEN`, `HGET` and similar until one of those commands runs (tested: it then disappears). `GET` on strings is always correct.
- **Expiry cost:** each purge scans every key that has a TTL, so cost grows with the number of expiring keys.
- **Command coverage:** a subset of Redis. `KEYS` ignores its pattern, `DEL` takes one key, `LGET` replaces `LRANGE`, `EXPIRE` replies `OK` rather than an integer, and errors use `-Error:` rather than `-ERR`. There is no `CONFIG`, pub/sub, transactions or authentication.
- **Type safety:** list and hash commands do not return `WRONGTYPE` when the key already exists with another type.
- **Persistence:** snapshots only (no append-only log), so up to 5 minutes of writes can be lost on a hard crash. Version-1 dump files cannot be loaded.
- **Shutdown:** the `SIGINT` handler performs the dump itself, which is not strictly async-signal-safe; finished connection threads are only joined at exit.

**Roadmap**

1. Replace thread-per-client with an `epoll` event loop (or a thread pool with sharded locks) and benchmark at 50+ clients.
2. Enforce TTLs on every read path, and track expirations in a min-heap or timer wheel instead of scanning.
3. Add `LRANGE`, pattern-aware `KEYS`, multi-key `DEL`, integer replies matching Redis, and `WRONGTYPE` errors.
4. Add an append-only file for finer-grained durability.
5. Profile the request path and reduce string copies.
