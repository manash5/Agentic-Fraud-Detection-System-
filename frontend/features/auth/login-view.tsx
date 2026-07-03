"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { AnimatePresence, motion } from "framer-motion";
import { ArrowLeft, Loader2, Phone, ShieldCheck, X } from "lucide-react";
import { toast } from "sonner";
import { Brand } from "@/components/shared/brand";
import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { BiometricPrompt } from "@/features/auth/biometric-prompt";
import { MpinKeypad } from "@/features/auth/mpin-keypad";
import { loginWithBiometric, loginWithMpin } from "@/services/authService";
import { DEMO_CREDENTIALS, db } from "@/mock/db";
import { setAuthUser } from "@/lib/auth";
import { ApiError } from "@/services/http";
import { initials } from "@/lib/format";

type Step = "mobile" | "pin" | "biometric";

export function LoginView() {
  const router = useRouter();
  const [step, setStep] = React.useState<Step>("mobile");
  const [mobile, setMobile] = React.useState("");
  const [pin, setPin] = React.useState("");
  const [pinError, setPinError] = React.useState(false);
  const [loading, setLoading] = React.useState(false);

  const knownCustomer =
    mobile.trim() === DEMO_CREDENTIALS.mobile
      ? db.customers.find((c) => c.mobile === DEMO_CREDENTIALS.mobile)
      : undefined;

  const onContinue = (e: React.FormEvent) => {
    e.preventDefault();
    if (mobile.trim().length < 10) {
      toast.error("Enter a valid 10-digit mobile number");
      return;
    }
    setStep("pin");
  };

  const finishLogin = (user: Awaited<ReturnType<typeof loginWithMpin>>) => {
    setAuthUser(user);
    toast.success(`Welcome back, ${user.name.split(" ")[0]}`);
    router.push("/dashboard");
  };

  const onPinComplete = async (value: string) => {
    setLoading(true);
    try {
      const user = await loginWithMpin(mobile, value);
      finishLogin(user);
    } catch (err) {
      setPinError(true);
      toast.error(err instanceof ApiError ? err.message : "Login failed.");
      setTimeout(() => {
        setPin("");
        setPinError(false);
      }, 400);
    } finally {
      setLoading(false);
    }
  };

  const onBiometricSuccess = async () => {
    try {
      const user = await loginWithBiometric();
      finishLogin(user);
    } catch {
      toast.error("Biometric login failed. Try your mPIN instead.");
      setStep("pin");
    }
  };

  const fillDemo = () => {
    setMobile(DEMO_CREDENTIALS.mobile);
    toast.info(`Demo mPIN is ${DEMO_CREDENTIALS.mpin}`);
  };

  return (
    <div className="grid min-h-screen lg:grid-cols-2">
      {/* Brand panel */}
      <div className="relative hidden flex-col justify-between overflow-hidden bg-sidebar p-12 text-sidebar-foreground lg:flex">
        <div
          className="pointer-events-none absolute inset-0 opacity-[0.07]"
          style={{
            backgroundImage:
              "radial-gradient(circle at 1px 1px, white 1px, transparent 0)",
            backgroundSize: "24px 24px",
          }}
        />
        <Brand invert subtitle />
        <div className="relative z-10 max-w-md space-y-6">
          <h1 className="text-4xl font-semibold leading-tight tracking-tight text-white">
            Smart banking, secured in real time.
          </h1>
          <p className="text-sidebar-foreground/70">
            Transfer funds, pay bills, and manage your accounts with bank-grade
            security — protected by continuous fraud monitoring on every
            transaction.
          </p>
          <div className="grid grid-cols-3 gap-4 pt-4">
            {[
              { k: "99.98%", v: "Uptime" },
              { k: "<800ms", v: "Processing" },
              { k: "24/7", v: "Support" },
            ].map((s) => (
              <div key={s.v}>
                <div className="text-2xl font-semibold text-white">{s.k}</div>
                <div className="text-xs uppercase tracking-wider text-sidebar-foreground/50">
                  {s.v}
                </div>
              </div>
            ))}
          </div>
        </div>
        <div className="relative z-10 flex items-center gap-2 text-sm text-sidebar-foreground/60">
          <ShieldCheck className="h-4 w-4" />
          256-bit encryption · NRB compliant · Global IME Bank
        </div>
      </div>

      {/* Form panel */}
      <div className="relative flex items-center justify-center p-6 sm:p-12">
        <div className="absolute right-6 top-6 flex items-center gap-2">
          <ThemeToggle />
        </div>
        <div className="w-full max-w-sm">
          <div className="mb-8 lg:hidden">
            <Brand />
          </div>

          <AnimatePresence mode="wait">
            {step === "mobile" && (
              <motion.div
                key="mobile"
                initial={{ opacity: 0, x: -12 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: -12 }}
                transition={{ duration: 0.25 }}
              >
                <h2 className="text-2xl font-semibold tracking-tight">
                  Log in to GIMEBiz
                </h2>
                <p className="mt-1.5 text-sm text-muted-foreground">
                  Enter your registered mobile number to continue.
                </p>

                <form onSubmit={onContinue} className="mt-8 space-y-4">
                  <div className="space-y-1.5">
                    <Label htmlFor="mobile">Mobile Number</Label>
                    <div className="relative">
                      <Phone className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                      <Input
                        id="mobile"
                        inputMode="numeric"
                        className="pl-9"
                        placeholder="98xxxxxxxx"
                        maxLength={10}
                        value={mobile}
                        onChange={(e) =>
                          setMobile(e.target.value.replace(/\D/g, "").slice(0, 10))
                        }
                        autoFocus
                      />
                    </div>
                  </div>

                  <Button type="submit" size="lg" className="w-full">
                    Continue
                  </Button>
                </form>

                <button
                  onClick={fillDemo}
                  className="mt-4 w-full rounded-lg border border-dashed border-border bg-muted/40 p-3 text-left text-xs text-muted-foreground transition-colors hover:bg-muted"
                >
                  <span className="font-medium text-foreground">
                    Demo access
                  </span>{" "}
                  — click to autofill. mPIN is{" "}
                  <span className="font-mono font-semibold text-primary">
                    {DEMO_CREDENTIALS.mpin}
                  </span>
                </button>

                <p className="mt-6 text-center text-xs text-muted-foreground">
                  Bank staff?{" "}
                  <a
                    href="/admin"
                    className="font-medium text-primary hover:underline"
                  >
                    Open Admin Portal
                  </a>
                </p>
              </motion.div>
            )}

            {step === "pin" && (
              <motion.div
                key="pin"
                initial={{ opacity: 0, x: 12 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: 12 }}
                transition={{ duration: 0.25 }}
              >
                <div className="mb-6 flex items-center justify-between">
                  <button
                    onClick={() => {
                      setStep("mobile");
                      setPin("");
                    }}
                    className="flex h-9 w-9 items-center justify-center rounded-full text-muted-foreground hover:bg-accent"
                  >
                    <ArrowLeft className="h-4 w-4" />
                  </button>
                </div>

                <div className="flex flex-col items-center text-center">
                  <div className="flex h-16 w-16 items-center justify-center rounded-full bg-primary text-lg font-semibold text-primary-foreground">
                    {initials(knownCustomer?.name ?? "Global IME")}
                  </div>
                  <h2 className="mt-3 text-xl font-semibold tracking-tight">
                    {knownCustomer
                      ? `Hi, ${knownCustomer.name.split(" ")[0]}!`
                      : "Enter your mPIN"}
                  </h2>
                  <p className="mt-1 text-sm text-muted-foreground">
                    ••••••{mobile.slice(-4)}
                  </p>
                </div>

                <div className="mt-8">
                  <MpinKeypad
                    value={pin}
                    onChange={setPin}
                    onComplete={onPinComplete}
                    disabled={loading}
                    error={pinError}
                    onBiometric={() => setStep("biometric")}
                  />
                </div>

                {loading && (
                  <div className="mt-4 flex justify-center">
                    <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
                  </div>
                )}

                <button
                  onClick={() => setStep("biometric")}
                  className="mt-6 w-full text-center text-sm font-medium text-primary hover:underline"
                >
                  Use Face ID / Fingerprint instead
                </button>
              </motion.div>
            )}

            {step === "biometric" && (
              <motion.div
                key="biometric"
                initial={{ opacity: 0, x: 12 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: 12 }}
                transition={{ duration: 0.25 }}
              >
                <div className="mb-2 flex items-center justify-between">
                  <h2 className="text-xl font-semibold tracking-tight">
                    Biometric Login
                  </h2>
                  <button
                    onClick={() => setStep("pin")}
                    className="flex h-9 w-9 items-center justify-center rounded-full text-muted-foreground hover:bg-accent"
                  >
                    <X className="h-4 w-4" />
                  </button>
                </div>
                <BiometricPrompt
                  label="Scan your face or fingerprint to continue"
                  onSuccess={onBiometricSuccess}
                  onCancel={() => setStep("pin")}
                />
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>
    </div>
  );
}
