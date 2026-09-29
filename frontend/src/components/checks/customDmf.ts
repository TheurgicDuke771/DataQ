import type { DmfCapability } from '../../api/connections';

/** What the connection's last test saw of the role's custom DMFs, for the function field. */
export function customDmfListingNote(capability: DmfCapability | undefined): string {
  if (capability === undefined || capability.custom_functions === undefined) {
    return 'Custom DMFs haven’t been listed for this connection yet — re-test the connection to list the ones its role can use. You can still type a name.';
  }
  if (capability.custom_functions === null) {
    return (
      capability.custom_functions_reason ?? 'Custom DMFs couldn’t be listed for this connection.'
    );
  }
  const unlisted = capability.custom_functions_unlisted
    ? ` ${capability.custom_functions_unlisted} more have quoted lower-case names DataQ can’t reference.`
    : '';
  if (capability.custom_functions.length === 0) {
    return `No custom DMFs were visible to this connection’s role at its last test — create one, grant the role USAGE on it and on its database and schema, then re-test the connection.${unlisted}`;
  }
  const truncated = capability.custom_functions_truncated ? ' (the first 200 only)' : '';
  return `Suggestions are the ${capability.custom_functions.length} custom DMFs${truncated} the role could use at the connection’s last test.${unlisted}`;
}
