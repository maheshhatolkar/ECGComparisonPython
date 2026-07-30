import { getApiUrl } from "../config";
import React, { useEffect, useState } from 'react';
import { View, Text, FlatList, TouchableOpacity, ActivityIndicator, RefreshControl } from 'react-native';

export default function RecordsScreen({ route, navigation }) {
  const { user, token } = route.params || {};
  const [records, setRecords] = useState([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    fetchRecords();
  }, []);

  const fetchRecords = async () => {
    setLoading(true);
    try {
      const resp = await fetch(`${getApiUrl()}/records`);
      if (resp.ok) {
        const data = await resp.json();
        setRecords(Array.isArray(data) ? data : []);
      } else {
        setRecords([]);
      }
    } catch (e) {
      console.error("Failed to fetch records:", e);
      setRecords([]);
    }
    setLoading(false);
  };

  return (
    <View style={{ flex: 1, padding: 16, backgroundColor: '#fff' }}>
      {/* Header Navigation Tabs */}
      <View style={{ flexDirection: 'row', backgroundColor: '#e9ecef', borderRadius: 8, padding: 4, marginBottom: 16 }}>
        <TouchableOpacity onPress={() => navigation.navigate('Analyze', { user, token })} style={{ flex: 1, paddingVertical: 8, alignItems: 'center' }}>
          <Text style={{ color: '#495057', fontWeight: '600' }}>Analyze</Text>
        </TouchableOpacity>
        <TouchableOpacity style={{ flex: 1, paddingVertical: 8, backgroundColor: '#007bff', borderRadius: 6, alignItems: 'center' }}>
          <Text style={{ color: '#fff', fontWeight: 'bold' }}>Records</Text>
        </TouchableOpacity>
        <TouchableOpacity onPress={() => navigation.navigate('Compare', { user, token })} style={{ flex: 1, paddingVertical: 8, alignItems: 'center' }}>
          <Text style={{ color: '#495057', fontWeight: '600' }}>Compare</Text>
        </TouchableOpacity>
        {user?.role === 'Administrator' && (
          <TouchableOpacity onPress={() => navigation.navigate('AdminAnalysis', { user, token })} style={{ flex: 1, paddingVertical: 8, alignItems: 'center' }}>
            <Text style={{ color: '#495057', fontWeight: '600' }}>Admin</Text>
          </TouchableOpacity>
        )}
        <TouchableOpacity onPress={() => navigation.replace('Login')} style={{ paddingHorizontal: 8, paddingVertical: 8, backgroundColor: '#dc3545', borderRadius: 6, marginLeft: 4 }}>
          <Text style={{ color: '#fff', fontWeight: 'bold', fontSize: 11 }}>Logout</Text>
        </TouchableOpacity>
      </View>

      <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <Text style={{ fontSize: 18, fontWeight: 'bold', color: '#212529' }}>Saved ECG Records</Text>
        <TouchableOpacity onPress={fetchRecords} style={{ backgroundColor: '#6c757d', paddingHorizontal: 12, paddingVertical: 6, borderRadius: 4 }}>
          <Text style={{ color: '#fff', fontWeight: '600' }}>Refresh</Text>
        </TouchableOpacity>
      </View>

      {loading && records.length === 0 ? (
        <ActivityIndicator size="large" color="#007bff" style={{ marginTop: 24 }} />
      ) : records.length === 0 ? (
        <View style={{ alignItems: 'center', marginTop: 32 }}>
          <Text style={{ color: '#6c757d', fontSize: 16 }}>No records stored in database.</Text>
        </View>
      ) : (
        <FlatList
          data={records}
          keyExtractor={(item) => String(item.id)}
          refreshControl={<RefreshControl refreshing={loading} onRefresh={fetchRecords} />}
          renderItem={({ item }) => (
            <TouchableOpacity
              onPress={() => navigation.navigate('Analyze', { user, token, recordId: item.id })}
              style={{ padding: 14, backgroundColor: '#f8f9fa', borderRadius: 8, marginBottom: 10, borderWidth: 1, borderColor: '#dee2e6' }}
            >
              <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginBottom: 4 }}>
                <Text style={{ fontWeight: 'bold', fontSize: 15, color: '#007bff' }}>
                  Record #{item.id} - {item.patient_id || 'Anonymous'}
                </Text>
                <Text style={{ fontSize: 12, color: '#6c757d' }}>{item.ecg_datetime || ''}</Text>
              </View>

              <Text style={{ fontSize: 13, color: '#495057' }}>
                HR: {item.heart_rate_bpm ? `${item.heart_rate_bpm} bpm` : 'N/A'} | PR: {item.pr_interval_ms ? `${item.pr_interval_ms} ms` : 'N/A'} | QRS: {item.qrs_duration_ms ? `${item.qrs_duration_ms} ms` : 'N/A'}
              </Text>

              {item.root_cause && (
                <Text style={{ fontSize: 12, color: '#28a745', marginTop: 4 }}>
                  Cause: {item.root_cause}
                </Text>
              )}
            </TouchableOpacity>
          )}
        />
      )}
    </View>
  );
}
