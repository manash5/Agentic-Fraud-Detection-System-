import type { AuthUser } from "@/types/banking";
import { setAuthSession, type DemoProfile } from "@/lib/auth";
import { request } from "./http";

interface LoginResponse {
  token: string;
  user: AuthUser;
}

interface ProfileLoginResponse extends LoginResponse {
  profile: DemoProfile;
}

/** GET /auth/demo-profiles — the fixed profiles for the login picker. */
export function getDemoProfiles(): Promise<DemoProfile[]> {
  return request<DemoProfile[]>("/auth/demo-profiles");
}

/** POST /auth/login-profile — one-click demo login; stores session + prefill. */
export async function loginWithProfile(profileId: string): Promise<DemoProfile> {
  const { token, user, profile } = await request<ProfileLoginResponse>(
    "/auth/login-profile",
    { method: "POST", body: { profileId } },
  );
  setAuthSession({ token, user, profile });
  return profile;
}

/** POST /auth/login-mpin — mobile + 4-digit mPIN; stores the session token. */
export async function loginWithMpin(
  mobile: string,
  mpin: string,
): Promise<AuthUser> {
  const { token, user } = await request<LoginResponse>("/auth/login-mpin", {
    method: "POST",
    body: { mobile: mobile.trim(), mpin },
  });
  setAuthSession({ token, user });
  return user;
}

/** POST /auth/login-biometric — trusted-device unlock for a registered mobile. */
export async function loginWithBiometric(mobile: string): Promise<AuthUser> {
  const { token, user } = await request<LoginResponse>(
    "/auth/login-biometric",
    { method: "POST", body: { mobile: mobile.trim() } },
  );
  setAuthSession({ token, user });
  return user;
}

/** GET /auth/customer-preview — registered name for the login screen. */
export function getCustomerPreview(mobile: string): Promise<{ name: string }> {
  return request<{ name: string }>("/auth/customer-preview", {
    params: { mobile: mobile.trim() },
  });
}

/** POST /auth/verify-mpin — transfer-time re-authentication. */
export function verifyMpin(mpin: string): Promise<{ verified: boolean }> {
  return request<{ verified: boolean }>("/auth/verify-mpin", {
    method: "POST",
    body: { mpin },
  });
}

/** POST /auth/logout — invalidates the Redis session. */
export async function logout(): Promise<void> {
  try {
    await request<{ ok: boolean }>("/auth/logout", { method: "POST" });
  } finally {
    setAuthSession(null);
  }
}
