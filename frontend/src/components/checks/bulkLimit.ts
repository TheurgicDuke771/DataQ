/** The backend's cap on one bulk check request (`check_service.BULK_CHECKS_MAX`). */
export const BULK_CHECKS_MAX = 500;

/** More checks selected than one bulk request takes — the actions are blocked, not sent. */
export function exceedsBulkLimit(selectedCount: number): boolean {
  return selectedCount > BULK_CHECKS_MAX;
}
