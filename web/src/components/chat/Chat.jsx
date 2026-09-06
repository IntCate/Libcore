export default function Chat({ data = [], emit }) {
  return (
    <div className="chat">
      <div className="chat-messages">
        {data.map((m, i) => (
          <div key={i} className={`chat-msg chat-msg-${m.role || "agent"}`}>
            {m.delta ?? m.content ?? ""}
          </div>
        ))}
      </div>
      <form
        className="chat-input"
        onSubmit={(e) => {
          e.preventDefault();
          const input = e.target.elements.msg;
          if (input.value.trim()) {
            emit("onSend", { text: input.value });
            input.value = "";
          }
        }}
      >
        <input name="msg" placeholder="输入消息..." />
        <button type="submit">发送</button>
      </form>
    </div>
  );
}
