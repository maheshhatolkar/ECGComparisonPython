import { getApiUrl } from "../config";
import React, { useEffect, useState } from 'react';
import { View, Text, FlatList, TouchableOpacity, ActivityIndicator, ScrollView } from 'react-native';

export default function AdminAnalysisScreen({ route, navigation }) {
  const { user, token } = route.params || {};
  const [tables, setTables] = useState([]);
  const [selectedTable, setSelectedTable] = useState(null);
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);

  const fetchTables = async () => {
    setLoading(true);
    try {
      const resp = await fetch(`${getApiUrl()}/tables`, {
        headers: { Authorization: `Bearer ${token}` }
      });
      if (resp.ok) {
        const json = await resp.json();
        setTables(Array.isArray(json) ? json : []);
      } else {
        setTables([]);
      }
    } catch (e) {
      console.error("Failed to fetch tables:", e);
      setTables([]);
    }
    setLoading(false);
  };

  const fetchTableData = async (tableName) => {
    setSelectedTable(tableName);
    setLoading(true);
    try {
      const resp = await fetch(`${getApiUrl()}/table/${tableName}`, {
        headers: { Authorization: `Bearer ${token}` }
      });
      if (resp.ok) {
        const json = await resp.json();
        setRows(Array.isArray(json) ? json : []);
      } else {
        setRows([]);
      }
    } catch (e) {
      console.error(`Failed to fetch data for ${tableName}:`, e);
      setRows([]);
    }
    setLoading(false);
  };

  useEffect(() => {
    fetchTables();
  }, []);

  return (
    <View style={{ flex: 1, padding: 16, backgroundColor: '#fff' }}>
      {/* Header Navigation Tabs */}
      <View style={{ flexDirection: 'row', backgroundColor: '#e9ecef', borderRadius: 8, padding: 4, marginBottom: 16 }}>
        <TouchableOpacity onPress={() => navigation.navigate('Analyze', { user, token })} style={{ flex: 1, paddingVertical: 8, alignItems: 'center' }}>
          <Text style={{ color: '#495057', fontWeight: '600' }}>Analyze</Text>
        </TouchableOpacity>
        <TouchableOpacity onPress={() => navigation.navigate('Records', { user, token })} style={{ flex: 1, paddingVertical: 8, alignItems: 'center' }}>
          <Text style={{ color: '#495057', fontWeight: '600' }}>Records</Text>
        </TouchableOpacity>
        <TouchableOpacity onPress={() => navigation.navigate('Compare', { user, token })} style={{ flex: 1, paddingVertical: 8, alignItems: 'center' }}>
          <Text style={{ color: '#495057', fontWeight: '600' }}>Compare</Text>
        </TouchableOpacity>
        <TouchableOpacity style={{ flex: 1, paddingVertical: 8, backgroundColor: '#007bff', borderRadius: 6, alignItems: 'center' }}>
          <Text style={{ color: '#fff', fontWeight: 'bold' }}>Admin</Text>
        </TouchableOpacity>
        <TouchableOpacity onPress={() => navigation.replace('Login')} style={{ paddingHorizontal: 8, paddingVertical: 8, backgroundColor: '#dc3545', borderRadius: 6, marginLeft: 4 }}>
          <Text style={{ color: '#fff', fontWeight: 'bold', fontSize: 11 }}>Logout</Text>
        </TouchableOpacity>
      </View>

      <Text style={{ fontSize: 18, fontWeight: 'bold', color: '#212529', marginBottom: 12 }}>Database Tables (Admin Inspector)</Text>

      {loading && <ActivityIndicator size="large" color="#007bff" style={{ marginVertical: 12 }} />}

      <View style={{ flexDirection: 'row', flexWrap: 'wrap', marginBottom: 16 }}>
        {Array.isArray(tables) && tables.map((t) => (
          <TouchableOpacity
            key={t}
            onPress={() => fetchTableData(t)}
            style={{
              backgroundColor: selectedTable === t ? '#007bff' : '#f8f9fa',
              paddingHorizontal: 12,
              paddingVertical: 8,
              borderRadius: 6,
              marginRight: 8,
              marginBottom: 8,
              borderWidth: 1,
              borderColor: selectedTable === t ? '#007bff' : '#dee2e6',
            }}
          >
            <Text style={{ color: selectedTable === t ? '#fff' : '#212529', fontWeight: '600' }}>{t}</Text>
          </TouchableOpacity>
        ))}
      </View>

      {selectedTable && (
        <View style={{ flex: 1 }}>
          <Text style={{ fontWeight: 'bold', fontSize: 15, marginBottom: 8, color: '#495057' }}>
            Rows in [{selectedTable}] ({rows.length} rows):
          </Text>

          {rows.length > 0 ? (
            <ScrollView horizontal={true} style={{ flex: 1, borderWidth: 1, borderColor: '#dee2e6', borderRadius: 6 }}>
              <View>
                <View style={{ flexDirection: 'row', backgroundColor: '#e9ecef', padding: 8, borderBottomWidth: 2, borderColor: '#dee2e6' }}>
                  {Object.keys(rows[0]).map((key) => (
                    <Text key={key} style={{ fontWeight: 'bold', width: 150, marginRight: 8, color: '#495057' }} numberOfLines={1}>
                      {key}
                    </Text>
                  ))}
                </View>
                <FlatList
                  data={rows}
                  keyExtractor={(_, index) => String(index)}
                  renderItem={({ item, index }) => (
                    <View style={{ flexDirection: 'row', padding: 8, borderBottomWidth: 1, borderColor: '#eee', backgroundColor: index % 2 === 0 ? '#fff' : '#f8f9fa' }}>
                      {Object.keys(rows[0]).map((key) => (
                        <Text key={key} style={{ width: 150, marginRight: 8, fontSize: 12, color: '#333' }} numberOfLines={2}>
                          {item[key] !== null && item[key] !== undefined ? String(item[key]) : 'null'}
                        </Text>
                      ))}
                    </View>
                  )}
                />
              </View>
            </ScrollView>
          ) : (
            <Text style={{ fontStyle: 'italic', color: '#6c757d' }}>No data available in this table.</Text>
          )}
        </View>
      )}
    </View>
  );
}
