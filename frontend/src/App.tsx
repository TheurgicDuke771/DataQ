import { Layout, Typography } from 'antd';

const { Header, Content } = Layout;

export function App() {
  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header
        style={{
          display: 'flex',
          alignItems: 'center',
          color: '#fff',
        }}
      >
        <Typography.Title level={4} style={{ color: '#fff', margin: 0 }}>
          DataQ
        </Typography.Title>
      </Header>
      <Content style={{ padding: 24 }}>
        <Typography.Paragraph>
          Data quality monitoring across Snowflake, ADLS Gen2, S3, and Unity Catalog.
        </Typography.Paragraph>
      </Content>
    </Layout>
  );
}
