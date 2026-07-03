"use client";

import * as React from "react";
import { Save } from "lucide-react";
import { toast } from "sonner";
import { PageHeader } from "@/components/admin/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Switch } from "@/components/ui/switch";
import { DISAGREEMENT_FORCE_OTP, STRUCTURING_BANDS } from "@/lib/trackb";
import { formatNPR } from "@/lib/format";

export default function AdminSettingsPage() {
  const [tauLow, setTauLow] = React.useState("0.30");
  const [tauHigh, setTauHigh] = React.useState("0.70");
  const [autoBlock, setAutoBlock] = React.useState(true);
  const [dualOtp, setDualOtp] = React.useState(true);
  const [disagreement, setDisagreement] = React.useState(true);

  return (
    <div className="max-w-2xl">
      <PageHeader
        title="Settings"
        description="Configure fraud engine thresholds and policies."
      />

      <Card className="mb-4">
        <CardContent className="p-5">
          <h3 className="text-sm font-semibold">Decision Thresholds</h3>
          <p className="mb-4 text-xs text-muted-foreground">
            Scores below τ-low pass automatically; above τ-high are blocked.
          </p>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label>τ-low (Pass ceiling)</Label>
              <Input
                value={tauLow}
                onChange={(e) => setTauLow(e.target.value)}
                className="font-mono"
              />
            </div>
            <div className="space-y-1.5">
              <Label>τ-high (Block floor)</Label>
              <Input
                value={tauHigh}
                onChange={(e) => setTauHigh(e.target.value)}
                className="font-mono"
              />
            </div>
          </div>
        </CardContent>
      </Card>

      <Card className="mb-4">
        <CardContent className="p-5">
          <h3 className="mb-2 text-sm font-semibold">Policies</h3>
          <PolicyRow
            label="Auto-block on high risk"
            desc="Immediately block transactions above τ-high."
            checked={autoBlock}
            onChange={setAutoBlock}
          />
          <Separator />
          <PolicyRow
            label="Dual-path OTP interlock"
            desc="Require both SMS and email OTP on challenge."
            checked={dualOtp}
            onChange={setDualOtp}
          />
          <Separator />
          <PolicyRow
            label="Force OTP on agent disagreement"
            desc={`Challenge when agent variance ≥ ${DISAGREEMENT_FORCE_OTP}.`}
            checked={disagreement}
            onChange={setDisagreement}
          />
        </CardContent>
      </Card>

      <Card className="mb-4">
        <CardContent className="p-5">
          <h3 className="text-sm font-semibold">NRB Structuring Bands</h3>
          <p className="mb-3 text-xs text-muted-foreground">
            Velocity agent watches for repeated transfers just under these
            reporting thresholds (SMURFING pattern).
          </p>
          <div className="flex flex-wrap gap-2">
            {STRUCTURING_BANDS.map((band) => (
              <span
                key={band}
                className="rounded-md border bg-muted/40 px-3 py-1.5 font-mono text-xs font-medium"
              >
                {formatNPR(band, false)}
              </span>
            ))}
          </div>
        </CardContent>
      </Card>

      <Button onClick={() => toast.success("Settings saved")}>
        <Save className="h-4 w-4" /> Save changes
      </Button>
    </div>
  );
}

function PolicyRow({
  label,
  desc,
  checked,
  onChange,
}: {
  label: string;
  desc: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <div className="flex items-center justify-between py-3">
      <div>
        <div className="text-sm font-medium">{label}</div>
        <div className="text-xs text-muted-foreground">{desc}</div>
      </div>
      <Switch checked={checked} onCheckedChange={onChange} />
    </div>
  );
}
