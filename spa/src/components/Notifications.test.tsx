/* @vitest-environment jsdom */
import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { NotificationProvider, useNotifications } from "./Notifications";
import { PageBanner } from "./PageBanner";

function NotificationProbe() {
  const { notify } = useNotifications();

  return (
    <button
      type="button"
      onClick={() =>
        notify({
          title: "Quota Warning",
          message: "eu-west-1 runtime is above 80% utilisation.",
          severity: "warning",
        })
      }
    >
      Trigger notification
    </button>
  );
}

describe("Notifications", () => {
  it("renders predictable severity surfaces for notifications and banners", () => {
    render(
      <NotificationProvider>
        <NotificationProbe />
        <PageBanner title="Tenant Context Missing" severity="warning">
          Tenant context is required for this route.
        </PageBanner>
      </NotificationProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: /trigger notification/i }));

    expect(screen.getByText("Quota Warning")).toBeInTheDocument();
    expect(screen.getByText("eu-west-1 runtime is above 80% utilisation.")).toBeInTheDocument();
    expect(screen.getByText("Tenant Context Missing")).toBeInTheDocument();
    expect(screen.getByText("Tenant context is required for this route.")).toBeInTheDocument();
  });
});
