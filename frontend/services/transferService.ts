import type { FraudResult, Transaction, TransferRequest } from "@/types/banking";
import { db, mockHelpers } from "@/mock/db";
import { CITIES, DEVICES } from "@/mock/constants";
import { txnTypeForTransfer } from "@/lib/trackb";
import { mockRequest } from "./http";

export interface FraudCheckInput extends TransferRequest {
  hour?: number;
}

/**
 * POST /fraud-check
 * Runs the (mock) 5-agent Track B fraud pipeline (velocity, geo, behaviour,
 * graph, synthesis) and returns a decision. Decision is biased by amount,
 * hour, txn_type and a little randomness so the demo produces PASS / OTP /
 * BLOCK outcomes realistically.
 */
export function fraudCheck(input: FraudCheckInput): Promise<FraudResult> {
  return mockRequest(
    () => {
      const hour = input.hour ?? new Date().getHours();
      const foreign = false;
      const txnType = txnTypeForTransfer(input.destination, input.recipientBank, input.mode);
      let baseRisk = 0.1 + Math.random() * 0.15;
      if (input.amount >= 100000) baseRisk += 0.25;
      else if (input.amount >= 50000) baseRisk += 0.12;
      if (hour < 6 || hour > 22) baseRisk += 0.12;
      if (input.destination === "other_bank") baseRisk += 0.08;
      if (input.destination === "wallet") baseRisk += 0.05;
      baseRisk = mockHelpers.clamp(baseRisk + (Math.random() - 0.5) * 0.15);

      const analysis = mockHelpers.buildFraud(
        baseRisk,
        input.amount,
        hour,
        foreign,
        txnType,
      );

      return {
        reference: mockHelpers.refNo(),
        score: analysis.synthesis.finalRisk,
        decision: analysis.synthesis.decision,
        pattern: analysis.synthesis.pattern,
        analysis,
      };
    },
    { min: 600, max: 1400 },
  );
}

/**
 * POST /otp/verify (transfer step)
 * Track B dual-path interlock: both the SMS and email codes must verify.
 */
export function verifyTransferOtp(
  smsCode: string,
  emailCode: string,
): Promise<{ verified: boolean }> {
  return mockRequest(
    () => ({
      verified: smsCode.trim().length === 6 && emailCode.trim().length === 6,
    }),
    { min: 500, max: 1200 },
  );
}

/**
 * POST /transfer
 * Commits the transfer: debits the account, records a transaction, returns it.
 */
export function commitTransfer(
  req: TransferRequest,
  fraud: FraudResult,
): Promise<Transaction> {
  return mockRequest(
    () => {
      const account = db.accounts.find((a) => a.id === req.fromAccountId)!;
      const customer = db.customers.find((c) => c.id === account.customerId)!;
      account.balance = mockHelpers.round2(account.balance - req.amount);

      const location = CITIES.find((c) => c.city === customer.city) ?? CITIES[0];
      const isWallet = req.destination === "wallet";
      const txnType = txnTypeForTransfer(req.destination, req.recipientBank, req.mode);
      const hour = new Date().getHours();
      const txn: Transaction = {
        id: mockHelpers.txnId(),
        reference: fraud.reference,
        customerId: customer.id,
        customerName: customer.name,
        accountNumber: account.accountNumber,
        counterparty: {
          name: req.recipientName,
          accountNumber: req.recipientAccount,
          bank: req.recipientBank,
          isWallet,
        },
        amount: req.amount,
        direction: "debit",
        type: req.mode === "bill" ? "payment" : isWallet ? "topup" : "transfer",
        channel: "mobile",
        status: "success",
        decision: fraud.decision,
        riskScore: fraud.score,
        latencyMs: mockHelpers.int(220, 640),
        location,
        device: DEVICES[1],
        ipAddress: "27.34.72.19",
        remarks: req.remarks || "Fund transfer",
        timestamp: new Date().toISOString(),
        fraud: fraud.analysis,
        txnType,
        counterpartyId: req.recipientAccount,
        fraudType: fraud.analysis.synthesis.fraudType,
        authMethod: "OTP",
        merchantCategoryCode: "6011",
        isVpn: false,
        isTor: false,
        impossibleTravel: false,
        prevTxnKm: mockHelpers.int(0, 30),
        prevTxnDeltaMin: mockHelpers.int(30, 2000),
        zScoreAmount: mockHelpers.round2(fraud.score * 3),
        txnCount1m: 1,
        dormancyBreak: false,
        nightFlag: hour < 6 || hour > 22,
        newCounterpartyFlag: !db.customers.some(
          (c) => c.accountNumber === req.recipientAccount,
        ),
        deviceId: `DEV-${mockHelpers.int(10000, 99999)}`,
      };
      db.transactions.unshift(txn);
      return txn;
    },
    { min: 500, max: 1100 },
  );
}
