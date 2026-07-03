import type { AuthUser } from "@/types/banking";
import { db, DEMO_CREDENTIALS } from "@/mock/db";
import { ApiError, mockRequest } from "./http";

function demoUser(): AuthUser {
  const customer = db.customers.find((c) => c.id === db.demoCustomerId)!;
  return {
    customerId: customer.id,
    name: customer.name,
    accountNumber: customer.accountNumber,
    mobile: customer.mobile,
  };
}

/**
 * POST /auth/login-mpin — phone number + 4-digit mPIN, mirroring Global
 * IME's mobile unlock flow (no separate OTP step once the device is trusted).
 */
export function loginWithMpin(mobile: string, mpin: string): Promise<AuthUser> {
  return mockRequest(
    () => {
      const matches =
        mobile.trim() === DEMO_CREDENTIALS.mobile && mpin === DEMO_CREDENTIALS.mpin;
      if (!matches) {
        throw new ApiError("Incorrect mobile number or mPIN.", 401);
      }
      return demoUser();
    },
    { min: 500, max: 1100 },
  );
}

/** POST /auth/login-biometric — Face ID / fingerprint mock unlock. */
export function loginWithBiometric(): Promise<AuthUser> {
  return mockRequest(() => demoUser(), { min: 900, max: 1500 });
}
