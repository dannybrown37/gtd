provider "oci" {
  tenancy_ocid     = var.tenancy_ocid
  user_ocid        = var.user_ocid
  fingerprint      = var.fingerprint
  private_key_path = var.private_key_path
  region           = var.region
}

data "oci_identity_availability_domains" "ads" {
  compartment_id = var.compartment_id
}

# NOTE: intentionally not using a `data "oci_core_images"` lookup for the
# instance's source image. Oracle publishes new Ubuntu image builds
# regularly, so "most recent" at apply time can silently differ from what's
# actually running -- and since `source_id` forces instance replacement on
# change, that lookup would non-deterministically destroy/recreate the box
# on unrelated `apply` runs. Pin explicitly instead; bump deliberately.

# --- Networking: VCN, public subnet, internet gateway, route table ---
# Mirrors the OCI console's "Connect public subnet to internet" quick action.
# display_name/dns_label below match what the OCI console auto-generates
# when you create these through the "create instance" wizard (timestamp-
# derived strings) -- dns_label is immutable (forces replacement if
# changed), so for a fresh deployment these will just become *your* run's
# own timestamp-derived values, which is fine; they're not meaningful
# identifiers, just Oracle's internal DNS naming.

resource "oci_core_vcn" "gtd" {
  compartment_id = var.compartment_id
  display_name   = var.vcn_display_name
  cidr_blocks    = ["10.0.0.0/16"]
  dns_label      = var.vcn_dns_label
}

resource "oci_core_internet_gateway" "gtd" {
  compartment_id = var.compartment_id
  vcn_id         = oci_core_vcn.gtd.id
  display_name   = "Internet Gateway ${var.vcn_display_name}"
  enabled        = true
}

resource "oci_core_route_table" "gtd" {
  compartment_id = var.compartment_id
  vcn_id         = oci_core_vcn.gtd.id
  display_name   = "Default Route Table for ${var.vcn_display_name}"

  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = oci_core_internet_gateway.gtd.id
  }
}

resource "oci_core_security_list" "gtd" {
  compartment_id = var.compartment_id
  vcn_id         = oci_core_vcn.gtd.id
  display_name   = "Default Security List for ${var.vcn_display_name}"

  egress_security_rules {
    destination = "0.0.0.0/0"
    protocol    = "all"
  }

  # Oracle's default ICMP rules (path MTU discovery + destination
  # unreachable), auto-created by the console wizard.
  ingress_security_rules {
    protocol = "1" # ICMP
    source   = "10.0.0.0/16"

    icmp_options {
      type = 3
      code = -1
    }
  }
  ingress_security_rules {
    protocol = "1" # ICMP
    source   = "0.0.0.0/0"

    icmp_options {
      type = 3
      code = 4
    }
  }

  # SSH, for initial setup and ongoing admin access.
  ingress_security_rules {
    protocol = "6" # TCP
    source   = "0.0.0.0/0"

    tcp_options {
      min = 22
      max = 22
    }
  }

  # Tailscale direct (UDP) connections. Without this, Tailscale still works
  # by falling back to its relay (DERP) servers, just with extra latency.
  ingress_security_rules {
    protocol = "17" # UDP
    source   = "0.0.0.0/0"

    udp_options {
      min = 41641
      max = 41641
    }
  }
}

resource "oci_core_subnet" "gtd" {
  compartment_id             = var.compartment_id
  vcn_id                     = oci_core_vcn.gtd.id
  display_name               = var.subnet_display_name
  cidr_block                 = "10.0.0.0/24"
  dns_label                  = var.subnet_dns_label
  route_table_id             = oci_core_route_table.gtd.id
  security_list_ids          = [oci_core_security_list.gtd.id]
  prohibit_public_ip_on_vnic = false
}

# --- Compute instance ---

resource "oci_core_instance" "gtd" {
  compartment_id      = var.compartment_id
  availability_domain = data.oci_identity_availability_domains.ads.availability_domains[0].name
  display_name        = var.instance_display_name
  shape               = var.instance_shape

  create_vnic_details {
    subnet_id        = oci_core_subnet.gtd.id
    display_name     = var.instance_display_name
    assign_public_ip = true
  }

  source_details {
    source_type = "image"
    source_id   = var.instance_image_id
  }

  metadata = {
    ssh_authorized_keys = file(var.ssh_public_key_path)
  }
}

output "public_ip" {
  description = "Public IP of the instance. Use this once for the initial SSH connection; day-to-day access should go over Tailscale."
  value       = oci_core_instance.gtd.public_ip
}
