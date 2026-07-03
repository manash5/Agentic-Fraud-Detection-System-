// Simulates network latency so mock services behave like real REST calls.
export function delay(min = 500, max = 2500): Promise<void> {
  const ms = Math.floor(Math.random() * (max - min + 1)) + min;
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

// Wraps a mock resolver with a realistic delay. Swap the body of each
// service function with a real `fetch` later without touching the UI.
export async function mockRequest<T>(
  resolver: () => T,
  opts?: { min?: number; max?: number },
): Promise<T> {
  await delay(opts?.min, opts?.max);
  return resolver();
}
