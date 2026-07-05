"use client";

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Save } from "lucide-react";
import { toast } from "sonner";
import { PageHeader } from "@/components/admin/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  getAdminSettings,
  saveAdminSettings,
  type ThresholdSettings,
} from "@/services/adminService";
import { ApiError } from "@/services/http";
import { STRUCTURING_BANDS } from "@/lib/trackb";
import { formatNPR } from "@/lib/format";

export default function AdminSettingsPage() {
  const queryClient = useQueryClient();
  const { data: settings, isLoading } = useQuery({
    queryKey: ["admin", "settings"],
    queryFn: getAdminSettings,
  });

  const [tauLow, setTauLow] = React.useState("");
  const [tauHigh, setTauHigh] = React.useState("");
  const [disagreement, setDisagreement] = React.useState("");

  React.useEffect(() => {
    if (settings) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setTauLow(String(settings.otpThreshold));
      setTauHigh(String(settings.blockThreshold));
      setDisagreement(String(settings.disagreementThreshold));
    }
  }, [settings]);

  const mutation = useMutation({
    mutationFn: (body: ThresholdSettings) => saveAdminSettings(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "settings"] });
      toast.success(
        "Thresholds saved — the live pipeline applies them within seconds.",
      );
    },
    onError: (err) =>
      toast.error(
        err instanceof ApiError ? err.message : "Could not save settings.",
      ),
  });

  const save = () => {
    const otpThreshold = Number(tauLow);
    const blockThreshold = Number(tauHigh);
    const disagreementThreshold = Number(disagreement);
    if (
      [otpThreshold, blockThreshold, disagreementThreshold].some(
        (v) => Number.isNaN(v) || v < 0 || v > 1,
      )
    ) {
      toast.error("Thresholds must be numbers between 0 and 1.");
      return;
    }
    mutation.mutate({ otpThreshold, blockThreshold, disagreementThreshold });
  };

  return (
    <div className="max-w-2xl">
      <PageHeader
        title="Settings"
        description="Decision thresholds applied live by the synthesis agent (API + Kafka orchestrator)."
      />

      <Card className="mb-4">
        <CardContent className="p-5">
          <h3 className="text-sm font-semibold">Decision Thresholds</h3>
          <p className="mb-4 text-xs text-muted-foreground">
            Scores below τ-low pass automatically; above τ-high are blocked;
            in between require OTP. Stored in Postgres and mirrored to Redis
            for both pipeline processes.
          </p>
          <div className="grid gap-4 sm:grid-cols-3">
            <div className="space-y-1.5">
              <Label>τ-low (OTP floor)</Label>
              <Input
                value={tauLow}
                onChange={(e) => setTauLow(e.target.value)}
                className="font-mono"
                disabled={isLoading}
              />
            </div>
            <div className="space-y-1.5">
              <Label>τ-high (Block floor)</Label>
              <Input
                value={tauHigh}
                onChange={(e) => setTauHigh(e.target.value)}
                className="font-mono"
                disabled={isLoading}
              />
            </div>
            <div className="space-y-1.5">
              <Label>Disagreement (force OTP)</Label>
              <Input
                value={disagreement}
                onChange={(e) => setDisagreement(e.target.value)}
                className="font-mono"
                disabled={isLoading}
              />
            </div>
          </div>
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

      <Button onClick={save} disabled={mutation.isPending || isLoading}>
        {mutation.isPending ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : (
          <Save className="h-4 w-4" />
        )}{" "}
        Save changes
      </Button>
    </div>
  );
}
