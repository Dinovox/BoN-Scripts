# Configuration réseau Battle of Nodes (BoN)
GATEWAY_URL = "https://gateway.battleofnodes.com/"

CHAIN_ID = "B"
DEFAULT_GAS_PRICE = 1_000_000_000
DEFAULT_TX_VERSION = 1

# Adresses smart contracts connus sur BoN
GOVERNANCE_SC = "erd1qqqqqqqqqqqqqqqpqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqylllslmq6y6"
DELEGATION_MANAGER_SC = "erd1qqqqqqqqqqqqqqqpqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqylllslmq6y6"
STAKING_SC = "erd1qqqqqqqqqqqqqqqpqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqllls0lczs7"

# Pool monitoring via nodes directs (observing squad — tunnel SSH ou LAN direct)
POOL_NUM_SHARDS  = 3
NODE_HOST        = "localhost"  # using ssh tunnel or direct LAN access to nodes
NODE_BASE_PORT   = 8080   # shard 0 → 8080, shard 1 → 8081, shard 2 → 8082
NODE_POOL_FIELD  = "erd_tx_pool_load"

# Wrapping EGLD → WEGLD (SC par shard — utiliser celui du shard du sender)
WEGLD_WRAP_SC_SHARD1 = "erd1qqqqqqqqqqqqqpgqhe8t5jewej70zupmh44jurgn29psua5l2jps3ntjj3"

