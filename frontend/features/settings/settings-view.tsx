"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import {
  Bell,
  Fingerprint,
  Globe,
  LogOut,
  Moon,
  Shield,
} from "lucide-react";
import { useTheme } from "next-themes";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { Switch } from "@/components/ui/switch";
import { useAuth } from "@/lib/auth";

export function SettingsView() {
  const router = useRouter();
  const { setUser } = useAuth();
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = React.useState(false);
  const [biometric, setBiometric] = React.useState(true);
  const [notifications, setNotifications] = React.useState(true);
  const [fraudAlerts, setFraudAlerts] = React.useState(true);

  // eslint-disable-next-line react-hooks/set-state-in-effect
  React.useEffect(() => setMounted(true), []);

  const logout = () => {
    setUser(null);
    router.replace("/login");
  };

  return (
    <div className="space-y-5">
      <h1 className="text-xl font-semibold tracking-tight">Settings</h1>

      <Card>
        <CardContent className="p-5">
          <h3 className="mb-2 text-sm font-semibold">Preferences</h3>
          <ToggleRow
            icon={Moon}
            label="Dark Mode"
            desc="Switch between light and dark theme"
            checked={mounted && theme === "dark"}
            onChange={(v) => setTheme(v ? "dark" : "light")}
          />
          <Separator />
          <ToggleRow
            icon={Bell}
            label="Push Notifications"
            desc="Transaction and account alerts"
            checked={notifications}
            onChange={setNotifications}
          />
          <Separator />
          <ToggleRow
            icon={Globe}
            label="Language"
            desc="English (Nepali coming soon)"
            checked={false}
            onChange={() => {}}
            disabled
          />
        </CardContent>
      </Card>

      <Card>
        <CardContent className="p-5">
          <h3 className="mb-2 text-sm font-semibold">Security</h3>
          <ToggleRow
            icon={Fingerprint}
            label="Biometric Login"
            desc="Use fingerprint or face unlock"
            checked={biometric}
            onChange={setBiometric}
          />
          <Separator />
          <ToggleRow
            icon={Shield}
            label="AI Fraud Alerts"
            desc="Real-time alerts on suspicious activity"
            checked={fraudAlerts}
            onChange={setFraudAlerts}
          />
        </CardContent>
      </Card>

      <Button
        variant="outline"
        className="w-full text-destructive hover:text-destructive"
        onClick={logout}
      >
        <LogOut className="h-4 w-4" /> Sign out
      </Button>
    </div>
  );
}

function ToggleRow({
  icon: Icon,
  label,
  desc,
  checked,
  onChange,
  disabled,
}: {
  icon: typeof Shield;
  label: string;
  desc: string;
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <div className="flex items-center justify-between py-3">
      <div className="flex items-center gap-3">
        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-muted text-muted-foreground">
          <Icon className="h-4 w-4" />
        </div>
        <div>
          <div className="text-sm font-medium">{label}</div>
          <div className="text-xs text-muted-foreground">{desc}</div>
        </div>
      </div>
      <Switch checked={checked} onCheckedChange={onChange} disabled={disabled} />
    </div>
  );
}
