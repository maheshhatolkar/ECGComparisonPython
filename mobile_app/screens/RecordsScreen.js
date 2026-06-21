import { API_URL } from "../config";
import React, { useEffect, useState } from 'react';
import { View, Text, FlatList, TouchableOpacity, Button } from 'react-native';

export default function RecordsScreen({ navigation }) {
  const [records, setRecords] = useState([]);
  useEffect(() => { fetchRecords(); }, []);
  const fetchRecords = async () => {
    const resp = await fetch(`${API_URL}/records`);
    const data = await resp.json();
    setRecords(data);
  };
  return (
    <View style={{ padding: 16 }}>
      <Button title="Refresh" onPress={fetchRecords} />
      <FlatList data={records} keyExtractor={(item) => String(item.id)} renderItem={({ item }) => (
        <TouchableOpacity onPress={() => navigation.navigate('Analyze', { recordId: item.id })}>
          <View style={{ padding: 8, borderBottomWidth: 1 }}>
            <Text>ID: {item.id} - {item.patient_id}</Text>
            <Text>{item.ecg_datetime}</Text>
          </View>
        </TouchableOpacity>
      )} />
    </View>
  );
}
