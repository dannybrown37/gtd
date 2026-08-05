variable "tenancy_ocid" {
  description = "OCID of your OCI tenancy."
  type        = string
}

variable "user_ocid" {
  description = "OCID of the OCI user running Terraform."
  type        = string
}

variable "fingerprint" {
  description = "Fingerprint of the API signing key uploaded to your OCI user."
  type        = string
}

variable "private_key_path" {
  description = "Path to the private API signing key (not the SSH key) for OCI auth."
  type        = string
}

variable "region" {
  description = "OCI region, e.g. us-ashburn-1."
  type        = string
}

variable "compartment_id" {
  description = "OCID of the compartment to create resources in (root compartment is fine for a personal account)."
  type        = string
}

variable "ssh_public_key_path" {
  description = "Path to the SSH public key to install on the instance for the ubuntu user."
  type        = string
  default     = "~/.ssh/id_rsa.pub"
}

variable "instance_shape" {
  description = "Always Free-eligible compute shape."
  type        = string
  default     = "VM.Standard.E2.1.Micro"
}

variable "instance_display_name" {
  description = "Name for the compute instance."
  type        = string
  default     = "GTD4ME"
}

variable "instance_image_id" {
  description = <<-EOT
    OCID of the boot image, e.g. a Canonical Ubuntu 24.04 image in your
    region. Find current options with:
      oci compute image list --compartment-id <compartment_id> \
        --operating-system "Canonical Ubuntu" --operating-system-version "24.04" \
        --shape "VM.Standard.E2.1.Micro"
    Pinned explicitly (not looked up dynamically) because source_id changes
    force instance replacement -- see the comment in main.tf.
  EOT
  type        = string
  default     = "ocid1.image.oc1.iad.aaaaaaaapwiifxzlyhsdqqqjxebqh67phjbthqrodefmy27t5mdwbvx4qk4a"
}

variable "vcn_display_name" {
  description = "Display name for the VCN."
  type        = string
  default     = "vcn-20260804-1952"
}

variable "vcn_dns_label" {
  description = "DNS label for the VCN (alphanumeric, immutable once set)."
  type        = string
  default     = "vcn08041959"
}

variable "subnet_display_name" {
  description = "Display name for the public subnet."
  type        = string
  default     = "subnet-20260804-1951"
}

variable "subnet_dns_label" {
  description = "DNS label for the subnet (alphanumeric, immutable once set)."
  type        = string
  default     = "subnet08042000"
}
