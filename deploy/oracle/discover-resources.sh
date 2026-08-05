#!/usr/bin/env bash
# Run this ON the Oracle VM (not locally) to discover the OCIDs of the
# resources you clicked through the console to create, and print the
# `terraform import` commands to adopt them into deploy/oracle/'s state.
#
# Uses instance principal auth -- no API key setup needed, the VM's own
# identity is enough to query the OCI API about itself and its network.
set -euo pipefail

if ! command -v oci >/dev/null 2>&1; then
  echo "==> Installing oci CLI"
  bash -c "$(curl -fsSL https://raw.githubusercontent.com/oracle/oci-cli/master/scripts/install/install.sh)" -- --accept-all-defaults
  export PATH="$HOME/bin:$PATH"
fi

AUTH="--auth instance_principal"
INSTANCE_ID=$(curl -fsSL -H "Authorization: Bearer Oracle" http://169.254.169.254/opc/v2/instance/ | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
COMPARTMENT_ID=$(oci compute instance get $AUTH --instance-id "$INSTANCE_ID" --query 'data."compartment-id"' --raw-output)

VNIC_ID=$(oci compute instance list-vnics $AUTH --instance-id "$INSTANCE_ID" --compartment-id "$COMPARTMENT_ID" --query 'data[0].id' --raw-output)
SUBNET_ID=$(oci network vnic get $AUTH --vnic-id "$VNIC_ID" --query 'data."subnet-id"' --raw-output)
SUBNET_INFO=$(oci network subnet get $AUTH --subnet-id "$SUBNET_ID")
VCN_ID=$(echo "$SUBNET_INFO" | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['vcn-id'])")
ROUTE_TABLE_ID=$(echo "$SUBNET_INFO" | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['route-table-id'])")
SECURITY_LIST_ID=$(echo "$SUBNET_INFO" | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['security-list-ids'][0])")
IGW_ID=$(oci network internet-gateway list $AUTH --compartment-id "$COMPARTMENT_ID" --vcn-id "$VCN_ID" --query 'data[0].id' --raw-output)

cat <<EOF

# ==== Discovered OCIDs ====
# compartment_id   = $COMPARTMENT_ID
# instance          = $INSTANCE_ID
# vnic              = $VNIC_ID
# subnet            = $SUBNET_ID
# vcn               = $VCN_ID
# route_table       = $ROUTE_TABLE_ID
# security_list     = $SECURITY_LIST_ID
# internet_gateway  = $IGW_ID

# ==== Run these locally, from deploy/oracle/, after 'terraform init' ====
terraform import oci_core_vcn.gtd $VCN_ID
terraform import oci_core_internet_gateway.gtd $IGW_ID
terraform import oci_core_route_table.gtd $ROUTE_TABLE_ID
terraform import oci_core_security_list.gtd $SECURITY_LIST_ID
terraform import oci_core_subnet.gtd $SUBNET_ID
terraform import oci_core_instance.gtd $INSTANCE_ID
EOF
