import { render, screen } from "@testing-library/react";
import { AnswerText } from "../components/AnswerText";

const noop = () => {};

describe("AnswerText", () => {
  it("turns known evidence tags into chips and leaves unknown ones as text", () => {
    render(
      <AnswerText text="Current draw is 36% high [e1]. See also [e9]." knownIds={new Set(["e1"])}
        openId={null} onCite={noop} />,
    );
    expect(screen.getByRole("button", { name: "Source e1" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Source e9" })).toBeNull();
    expect(screen.getByText(/See also \[e9\]/)).toBeInTheDocument();
  });

  it("renders a half-written tag as text while streaming", () => {
    render(<AnswerText text="Payload was 190 kg [e" knownIds={null} openId={null} onCite={noop} />);
    expect(screen.getByText("Payload was 190 kg [e")).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("renders bullet lines as a list and escapes markup", () => {
    const { container } = render(
      <AnswerText text={"Found:\n\n- current draw [e1]\n- payload <b>x</b>"} knownIds={null}
        openId={null} onCite={noop} />,
    );
    expect(container.querySelectorAll("li")).toHaveLength(2);
    expect(container.querySelector("b")).toBeNull();
    expect(screen.getByText(/payload <b>x<\/b>/)).toBeInTheDocument();
  });
});
