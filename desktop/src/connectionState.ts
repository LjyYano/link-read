/** A monotonically increasing revision prevents an old test response from
 * showing "connected" after the user edits an endpoint, model, or token.
 * No credentials are stored here.
 */
export class ConnectionRevision {
  private revision = 0
  invalidate(): void { this.revision += 1 }
  snapshot(): number { return this.revision }
  accepts(value: number): boolean { return value === this.revision }
}
