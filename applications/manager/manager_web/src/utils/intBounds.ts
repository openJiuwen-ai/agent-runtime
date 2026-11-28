/** MySQL / SQLAlchemy ``integer`` column range (signed INT32). */
export const INT32_MIN = -2_147_483_648;
export const INT32_MAX = 2_147_483_647;

export function isInt32(value: number): boolean {
  return Number.isInteger(value) && value >= INT32_MIN && value <= INT32_MAX;
}

export function isNonNegInt32(value: number): boolean {
  return Number.isInteger(value) && value >= 0 && value <= INT32_MAX;
}
