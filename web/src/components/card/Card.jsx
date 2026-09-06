export default function Card({ title, children, data = [] }) {
  return (
    <div className="card">
      {title && <div className="card-title">{title}</div>}
      <div className="card-body">{children}</div>
      {data.length > 0 && (
        <div className="card-data">
          {data.map((d, i) => (
            <div key={i}>{JSON.stringify(d)}</div>
          ))}
        </div>
      )}
    </div>
  );
}
