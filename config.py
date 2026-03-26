# Configuration réseau Battle of Nodes (BoN)
GATEWAY_URL = "https://gateway.battleofnodes.com"

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
WEGLD_WRAP_SC_SHARD =["erd1qqqqqqqqqqqqqpgqvc7gdl0p4s97guh498wgz75k8sav6sjfjlwqh679jy", "erd1qqqqqqqqqqqqqpgqhe8t5jewej70zupmh44jurgn29psua5l2jps3ntjj3","erd1qqqqqqqqqqqqqpgqmuk0q2saj0mgutxm4teywre6dl8wqf58xamqdrukln"]


WEGLD_USDC_SC_POOL = "erd1qqqqqqqqqqqqqpgqeel2kumf0r8ffyhth7pqdujjat9nx0862jpsg2pqaq"

# Adresses des déployeurs de smart contracts
DEPLOYER_ADDRESS =[
    "erd1zl4wdvn06a9hlwq0zncv4nww5x6ag54jjs5e2leeu6qrjy7rz2yqa9n8n7",
    "erd1v2c874mqpt6xcav24x7cpeygakmzcn6hj5yyuzm9ezjjens3me7sx00wtk",
    "erd13v8qfm6lae558nq9cj8q9p830jvtfq3wrj9p5a6vy9dvfa7mvmrqdc25mp"
]

FORWARDER_SC_ADDRESS = [
    "erd1qqqqqqqqqqqqqpgqcedk2c63prd9avzcf67ler6ev79clf7pz2yqm7d6a7",
    "erd1qqqqqqqqqqqqqpgqp0aq3n0qzwfac6qd4gx5hyrc62c2fmtlme7s0kj679",
    "erd1qqqqqqqqqqqqqpgq0z7zzhjqm2lgtvhqwxg4gtpjr9wq2j6pvmrqvhveyw"
]