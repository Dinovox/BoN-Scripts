import argparse
import os
import glob
import subprocess
import json
from pathlib import Path

import config

def main():
    parser = argparse.ArgumentParser(description="Deploy forwarder-blind contract to multiple shards")
    parser.add_argument("--shards", nargs="+", type=int, default=[0, 1, 2], help="Shards to deploy to")
    parser.add_argument("--wallets-dir", type=str, default="./fork-wallets", help="Base directory for wallets (expects /shard-0, /shard-1, etc.)")
    parser.add_argument("--wasm-path", type=str, default="./contract/dex-interactor/forwarder-blind-bon.wasm", help="Path to WASM file")
    parser.add_argument("--gas-limit", type=int, default=100000000, help="Gas limit for deployment")
    parser.add_argument("--out", type=str, default="forwarders.json", help="Output JSON file to store deployed addresses")
    parser.add_argument("--dry-run", action="store_true", help="Afficher les commandes sans les exécuter")
    args = parser.parse_args()

    wasm_path = Path(args.wasm_path)
    if not wasm_path.exists():
        print(f"[ERROR] WASM file not found at {wasm_path}")
        return

    results = {}
    
    for shard in args.shards:
        shard_dir = Path(args.wallets_dir) / f"shard-{shard}"
        if not shard_dir.exists():
            print(f"[WARNING] Directory {shard_dir} not found. Skipping shard {shard}.")
            continue
            
        pem_files = list(shard_dir.glob("*.pem"))
        if not pem_files:
            print(f"[WARNING] No PEM files found in {shard_dir}. Skipping.")
            continue
            
        pem_path = pem_files[0]
        outfile = f"deploy_shard_{shard}.json"
        
        print(f"[{shard}] Deploying from {pem_path}...")
        
        cmd = [
            "mxpy", "contract", "deploy",
            "--bytecode", str(wasm_path),
            "--pem", str(pem_path),
            "--gas-limit", str(args.gas_limit),
            "--proxy", config.GATEWAY_URL,
            "--chain", config.CHAIN_ID,
            "--metadata-payable-by-sc",  # requis : le DEX renvoie les tokens via callback SC
            "--recall-nonce",
            "--send",
            "--outfile", outfile
        ]
        
        print(f"Running: {' '.join(cmd)}")
        if args.dry_run:
            print(f"[DRY-RUN] shard-{shard}: commande affichée, aucun envoi.")
            continue

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            print(f"[ERROR] Deployment failed for Shard {shard}:")
            if result.stdout:
                print(result.stdout)
            if result.stderr:
                print(result.stderr)
            continue
            
        # Parse the outfile to get the contract address
        if os.path.exists(outfile):
            with open(outfile, "r") as f:
                data = json.load(f)
                contract_addr = data.get("contractAddress", "Unknown")
                tx_hash = data.get("emittedTransactionHash", "Unknown")
                
                print(f"[{shard}] Deployment Tx Hash: {tx_hash}")
                print(f"[{shard}] Contract Address: {contract_addr}")

                results[f"shard-{shard}"] = {
                    "deployer_pem": str(pem_path),
                    "tx_hash": tx_hash,
                    "contract": contract_addr
                }
        else:
            print(f"[WARNING] Outfile {outfile} not created!")
            
    with open(args.out, "w") as f:
        json.dump(results, f, indent=4)
        
    print(f"\nSaved deployment info to {args.out}")

if __name__ == "__main__":
    main()
