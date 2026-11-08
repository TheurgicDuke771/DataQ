# The Container Apps environment is referenced, not created: a free/trial subscription allows ONE,
# which other workloads may already use (ADR 0024).
data "azurerm_container_app_environment" "shared" {
  name                = "dataq-cae"
  resource_group_name = data.azurerm_resource_group.dataq.name

  # ── Residency assertion (G4 / #434) ──────────────────────────────────────── This environment is
  # SHARED and declared as a `data` source, so its region is whatever someone else created it in.
  lifecycle {
    postcondition {
      condition     = lower(replace(self.location, " ", "")) == lower(replace(var.azure_location, " ", ""))
      error_message = <<-EOT
        Residency mismatch (G4/#434): the shared Container Apps environment is in
        '${self.location}' but var.azure_location declares '${var.azure_location}'.
        Every Container App and Job in this stack inherits the ENVIRONMENT's region,
        so applying would place the app's compute outside the declared jurisdiction.
        There is deliberately no override for this one (unlike the shared DB's
        check): the environment's region is inherited by every Container App and
        Job here, so accepting a mismatch would relocate the whole deployment.
        Either move the workload back, or change azure_location deliberately.
      EOT
    }
  }
}
