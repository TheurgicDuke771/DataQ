# The Container Apps environment is SHARED with the harness: this subscription is
# capped at ONE Container App Environment, so we do NOT create one here — the
# harness Terraform owns it (renamed to the neutral `dataq-cae`). The app stack
# only REFERENCES it; the app's own apps/redis/migrate job run inside it but stay
# separate, dataq-app-* resources. The env lives in azure_location (westus2), so
# the app's container resources land there too.
data "azurerm_container_app_environment" "shared" {
  name                = "dataq-cae"
  resource_group_name = data.azurerm_resource_group.dataq.name

  # ── Residency assertion (G4 / #434) ────────────────────────────────────────
  #
  # This environment is SHARED and declared as a `data` source, so its region is
  # whatever someone else created it in — and every Container App and Job in this
  # stack inherits it (`containerapps.tf` sets `location` from it directly,
  # because a Job must sit in its environment's region).
  #
  # That inheritance is silent. Without this check, moving or recreating the
  # shared environment in another region would relocate ALL of the app's compute
  # — the API that reads warehouse credentials and renders failing-row samples —
  # with a clean `apply` and no signal. For a GDPR Ch. V control, "we did not
  # notice the jurisdiction changed" is the whole failure.
  #
  # A `postcondition` on the data source rather than a `validation` block: this is
  # a fact about the remote world, not about an input value, so it can only be
  # checked after the data source refreshes. (Named precisely because the sibling
  # assertion on the shared DATABASE below is a `check` block instead, for a
  # reason spelled out there — a maintainer adding a third should know the two
  # forms differ in whether they BLOCK.)
  lifecycle {
    postcondition {
      condition     = lower(replace(self.location, " ", "")) == lower(replace(var.azure_location, " ", ""))
      error_message = <<-EOT
        Residency mismatch (G4/#434): the shared Container Apps environment is in
        '${self.location}' but var.azure_location declares '${var.azure_location}'.
        Every Container App and Job in this stack inherits the ENVIRONMENT's region,
        so applying would place the app's compute outside the declared jurisdiction.
        Either move the workload back, or change azure_location deliberately and
        update the residency matrix in docs/security.md.
      EOT
    }
  }
}
