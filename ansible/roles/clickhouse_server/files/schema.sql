CREATE DATABASE IF NOT EXISTS poc ON CLUSTER '{cluster}';

-- Workload table written by the test app. One row per sequence number;
-- batch_id is the first seq of the INSERT batch it came in.
CREATE TABLE IF NOT EXISTS poc.events ON CLUSTER '{cluster}'
(
    seq            UInt64,
    batch_id       UInt64,
    ts             DateTime64(3, 'UTC'),
    writer_id      LowCardinality(String),
    ingest_replica LowCardinality(String) DEFAULT getMacro('replica'),
    payload        String
)
ENGINE = ReplicatedMergeTree('/clickhouse/tables/{shard}/{database}/{table}', '{replica}')
PARTITION BY toYYYYMMDD(ts)
ORDER BY (writer_id, seq);

-- Cluster-wide view (for demo queries; 1 shard means it reads one replica).
CREATE TABLE IF NOT EXISTS poc.events_all ON CLUSTER '{cluster}' AS poc.events
ENGINE = Distributed('{cluster}', poc, events, rand());

-- Written by the chaos runner to probe "can the cluster accept writes right now?"
CREATE TABLE IF NOT EXISTS poc.probe ON CLUSTER '{cluster}'
(
    ts       DateTime64(3, 'UTC') DEFAULT now64(3),
    scenario String,
    replica  LowCardinality(String) DEFAULT getMacro('replica')
)
ENGINE = ReplicatedMergeTree('/clickhouse/tables/{shard}/{database}/{table}', '{replica}')
ORDER BY ts
TTL toDateTime(ts) + INTERVAL 7 DAY;
